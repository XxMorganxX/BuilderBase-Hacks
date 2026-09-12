"""Three-repository scenario with the local model in the loop.

Same public bridge operations as the deterministic demo, plus a ReasoningWorker draining queued jobs
between steps so extraction, clarification drafting, semantic assessment, and mediation proposals come
from the configured local model. Every model output is validated, evidence-bound, and recorded.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from .bridge import Bridge
from .protocol import ContextPacket, EventType, Registration
from .reasoning import ReasoningWorker
from .service import Oracle
from .store import Store
from .transport import LocalTransport

PEOPLE = [("alice", "backend"), ("bob", "web"), ("carol", "analytics")]
SHARED = ["User.id", "AuthenticationResponse", "InternalEncoding"]


def narrator(enabled):
    def say(text=""):
        if enabled:
            print(text, file=sys.stderr, flush=True)

    return say


def inbox(bridge, kinds):
    return [m for m in bridge.db.execute("SELECT data FROM inbox ORDER BY cursor") for m in [json.loads(m["data"])] if m["type"] in kinds]


async def run_live(output_dir: Path, provider, narrate=False, pace=0.0):
    say = narrator(narrate)

    async def pause(seconds=None):
        if pace:
            await asyncio.sleep(seconds if seconds is not None else pace)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (output_dir / "oracle.db").exists():
        raise ValueError("Choose a new scenario directory; existing history will not be overwritten")
    store = Store(output_dir / "oracle.db")
    oracle = Oracle(store)
    worker = ReasoningWorker(oracle, provider)
    bridges, model_runs, steps = {}, [], []

    async def drain(step):
        results = await worker.drain()
        for result in results:
            model_runs.append({"step": step, **result})
        return results

    try:
        for human, project in PEOPLE:
            oracle.create_project(project, "owner", rules={"oracle_arbitration": True, "low_risk_subjects": ["InternalEncoding"]})
            oracle.grant("owner", project, human)
        for subject in SHARED:
            oracle.share("owner", "backend", "web", subject)
            oracle.share("owner", "backend", "analytics", subject)
        for human, project in PEOPLE:
            repository = output_dir / project
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            bridge = Bridge(output_dir / (human + "-bridge.db"), LocalTransport(oracle, human), human, project, repository)
            await bridge.register(
                Registration(
                    project_id=project,
                    repository=project,
                    human_id=human,
                    machine_id=human + "-simulated-machine",
                    agent_type="generic-live",
                    agent_session_id=human + "-live",
                    interests=["User.id"] + (["AuthenticationResponse", "InternalEncoding"] if human != "carol" else []),
                )
            )
            bridges[human] = bridge
        if hasattr(provider, "discover_model"):
            await provider.discover_model()
        say("ORACLE live demo — three people, three repositories, one local model")
        say(f"  model: {getattr(provider, 'model', None)}")
        say()
        say("[setup] alice→backend, bob→web, carol→analytics registered; backend shares User.id, AuthenticationResponse, InternalEncoding")
        await pause()

        # 1. Specification discovery (section 49): a vague prompt, a specific agent interpretation.
        packet = ContextPacket(
            session_id=bridges["alice"].session_id,
            task="Make login work",
            human_intent="Make login work.",
            agent_interpretation=(
                "Implement POST /login returning a JWT bearer access token with a 15-minute expiry; "
                "users are identified by UUID; expiration timestamps are UTC ISO-8601."
            ),
            implementation_plan="Add /login handler, JWT signing, UUID user lookup, and tests.",
        )
        await bridges["alice"].checkpoint(packet)
        extraction = await drain("knowledge_extraction")
        proposed = [f for f in store.records("facts", "backend") if f["lifecycle"] == "PROPOSED"]
        steps.append(
            {
                "step": "knowledge_extraction",
                "proposed_facts": [{k: f[k] for k in ["subject", "predicate", "value", "confidence", "visibility", "source"]} for f in proposed],
                "job_status": [r["status"] for r in extraction],
            }
        )
        assert proposed and all(f["visibility"] == "OWNER_ONLY" and f["source"] == "oracle_inference" for f in proposed)
        assert oracle.context("bob", bridges["bob"].session_id) == []  # tentative extraction never leaks
        say()
        say('[1] alice\'s agent checkpoints: human said "Make login work"; agent plans JWT bearer, 15-min expiry, UUID users, UTC timestamps')
        say(f"    model extracted {len(proposed)} tentative facts (PROPOSED, owner-only, never sent to other sessions):")
        for f in proposed:
            say(f"      · {f['subject']}.{f['predicate']} = {json.dumps(f['value'])}")
        await pause()

        # 2. Underspecified identity: three assumptions, a durable question, a model-drafted clarification.
        for human, value in [("alice", "UUID"), ("bob", "integer"), ("carol", "email")]:
            await bridges[human].emit(
                EventType.ASSUMPTION_UPDATE,
                {"subject": "User.id", "predicate": "type", "value": value, "visibility": "PUBLIC_CONTRACT"},
            )
        question = store.records("questions")[0]
        await pause()  # viewers see three sessions PAUSE_REQUESTED before the safe-checkpoint acks arrive
        for bridge in bridges.values():
            await bridge.poll()
            await bridge.emit(EventType.PAUSE_ACK, {"question_id": question["question_id"]})
        await drain("clarification")
        view = oracle.question_view("owner", question["question_id"])
        steps.append({"step": "clarification", "view": view})
        assert view["suggestion"] and view["suggestion"]["options"], "model must draft quick-select options"
        chosen = view["suggestion"].get("recommendation") or "UUID"
        say()
        say("[2] agents report User.id: alice=UUID, bob=integer, carol=email → UNDERSPECIFIED (no authority exists)")
        say("    Oracle pauses all three at a safe checkpoint; the model drafts the owner's question:")
        say(f"      Q: {view['suggestion']['question']}")
        if view["suggestion"].get("why_it_matters"):
            say(f"      why: {view['suggestion']['why_it_matters']}")
        for index, option in enumerate(view["suggestion"]["options"], 1):
            mark = "  ← recommended" if option == view["suggestion"].get("recommendation") else ""
            say(f"      [{index}] {option}{mark}")
        await pause()  # the drafted question is visible; the owner is "thinking"
        say(f"    owner answers: {chosen if chosen in {'UUID', 'integer', 'email'} else 'UUID'}  → human_decision, confidence 1.0")
        decision = oracle.decide(
            "owner",
            "backend",
            "User.id",
            "type",
            chosen if chosen in {"UUID", "integer", "email"} else "UUID",
            "Owner accepts the drafted recommendation for canonical identity",
            question_id=question["question_id"],
        )
        await acknowledge_all(bridges, decision)
        for human in ["alice", "bob", "carol"]:
            final = inbox(bridges[human], {"FINAL_DECISION"})[-1]["payload"]
            say(f"    → {human}: {final['instruction']}")
        await pause()

        # 3. Authentication mismatch: semantic assessment + model proposal, human keeps the final say.
        for human, protocol in [("alice", "JWT bearer"), ("bob", "session-cookie")]:
            await bridges[human].emit(
                EventType.INTERFACE_UPDATE,
                {
                    "name": "AuthenticationResponse",
                    "role": "PRODUCES" if human == "alice" else "CONSUMES",
                    "contract": {"authentication": protocol},
                    "visibility": "PUBLIC_CONTRACT",
                },
            )
        conflict = next(c for c in store.records("conflicts") if c["subject"] == "AuthenticationResponse")
        assert store.record("sessions", bridges["carol"].session_id)["work_state"] == "RUNNING"
        await pause()
        for human in ["alice", "bob"]:
            await bridges[human].emit(EventType.PAUSE_ACK, {"conflict_id": conflict["conflict_id"]})
        await drain("authentication_mediation")
        conflict = store.record("conflicts", conflict["conflict_id"])
        steps.append(
            {
                "step": "authentication_mediation",
                "type": conflict["type"],
                "assessment": conflict.get("assessment"),
                "suggested_proposal": conflict.get("suggested_proposal"),
                "state_after_model": conflict["state"],
            }
        )
        assert conflict["suggested_proposal"], "model must propose a resolution"
        assert conflict["state"] == "PAUSED", "authentication is not low-risk; the model must not open a round"
        say()
        say("[3] alice PRODUCES AuthenticationResponse.authentication=JWT bearer; bob CONSUMES it as session-cookie")
        say(f"    → CONFLICTING, typed {conflict['type']}; alice and bob paused, carol keeps working")
        assessment = conflict.get("assessment") or {}
        if assessment:
            say(f"    model assessment: {assessment['classification']} ({assessment['confidence']:.2f}, {assessment['severity']}) — {assessment['summary'][:140]}")
        proposal = conflict["suggested_proposal"]
        say(f"    model proposal: {json.dumps(proposal['value'])}  requires_human={proposal['requires_human']}")
        for project, change in (proposal.get("participant_changes") or {}).items():
            say(f"      · {project}: {change[:120]}")
        await pause()
        say("    owner adopts the proposal (propose --suggested); alice and bob ACCEPT; security → HUMAN_ESCALATION; owner decides")
        oracle.propose("owner", conflict["conflict_id"], use_suggestion=True)
        await pause()
        for human in ["alice", "bob"]:
            await bridges[human].emit(
                EventType.MEDIATION_RESPONSE,
                {"conflict_id": conflict["conflict_id"], "position": "ACCEPT", "reason": "Compatible with my component"},
            )
        assert store.record("conflicts", conflict["conflict_id"])["state"] == "HUMAN_ESCALATION"
        await pause()
        suggested = store.record("conflicts", conflict["conflict_id"])["suggested_proposal"]
        decision = oracle.decide(
            "owner",
            "backend",
            "AuthenticationResponse",
            "authentication",
            suggested["value"],
            "Security-sensitive authentication choice approved by the project owner",
            conflict_id=conflict["conflict_id"],
        )
        await acknowledge_all(bridges, decision)
        for human in ["alice", "bob"]:
            final = inbox(bridges[human], {"FINAL_DECISION"})[-1]["payload"]
            say(f"    → {human}: {final['instruction']}")
        contract = oracle.decide(
            "owner",
            "backend",
            "AuthenticationResponse",
            "fields",
            {"access_token": "string", "expires_at": "UTC timestamp", "user_id": "UUID"},
            "Owner approves the complete login response",
        )
        await acknowledge_all(bridges, contract)
        say("    owner also records the full login response {access_token, expires_at, user_id}; alice and bob acknowledge")
        await pause()

        # 4. Low-risk normalization: the model proposes, agents accept, Oracle arbitrates within its allowlist.
        for human, value in [("alice", "UTF-8"), ("bob", "utf8")]:
            await bridges[human].emit(
                EventType.DECISION_UPDATE,
                {"subject": "InternalEncoding", "predicate": "name", "value": value, "visibility": "PUBLIC_CONTRACT"},
            )
        conflict = next(c for c in store.records("conflicts") if c["subject"] == "InternalEncoding")
        await pause()
        for human in ["alice", "bob"]:
            await bridges[human].emit(EventType.PAUSE_ACK, {"conflict_id": conflict["conflict_id"]})
        await drain("low_risk_mediation")
        conflict = store.record("conflicts", conflict["conflict_id"])
        steps.append(
            {
                "step": "low_risk_mediation",
                "assessment": conflict.get("assessment"),
                "suggested_proposal": conflict.get("suggested_proposal"),
                "state_after_model": conflict["state"],
                "proposal_actor": (conflict.get("proposal") or {}).get("actor"),
            }
        )
        say()
        say("[4] alice decides InternalEncoding.name=UTF-8, bob decides utf8 → CONFLICTING on an owner-allowlisted low-risk subject")
        assessment = conflict.get("assessment") or {}
        if assessment:
            say(f"    model assessment: {assessment['classification']} ({assessment['confidence']:.2f}) — {assessment['summary'][:140]}")
        proposal = conflict["suggested_proposal"] or {}
        say(f"    model proposal: {json.dumps(proposal.get('value'))}  requires_human={proposal.get('requires_human')}")
        if conflict["state"] == "NEGOTIATION":
            say("    Oracle opened the bounded round itself (actor oracle-model)")
        else:
            say("    model asked for a human; owner adopts the suggestion")
        if conflict["state"] != "NEGOTIATION":
            # The model judged even this needs a human; the owner adopts its suggestion instead.
            oracle.propose("owner", conflict["conflict_id"], use_suggestion=True)
        await pause()
        for human in ["alice", "bob"]:
            result = await bridges[human].emit(
                EventType.MEDIATION_RESPONSE,
                {"conflict_id": conflict["conflict_id"], "position": "ACCEPT", "reason": "Equivalent spelling"},
            )
        decision = store.record("decisions", result["decision_id"])
        assert decision["authority"] == "oracle_arbitration"
        await acknowledge_all(bridges, decision)
        say(f"    both ACCEPT → Oracle arbitrates: InternalEncoding.name = {json.dumps(decision['value'])} (authority oracle_arbitration)")

        for human, project in PEOPLE:
            await bridges[human].poll()
            (output_dir / (human + "-context.json")).write_text(json.dumps(await bridges[human].context(), indent=2))
        states = {human: store.record("sessions", bridge.session_id)["work_state"] for human, bridge in bridges.items()}
        assert set(states.values()) == {"RUNNING"}
        assert all(c["state"] == "CLOSED" for c in store.records("conflicts"))
        say()
        say("[5] all decisions acknowledged → RESUME. What each session may now see (minimum necessary):")
        for human in ["alice", "bob", "carol"]:
            context = await bridges[human].context()
            say(f"    {human}: " + "; ".join(f"{f['subject']}.{f['predicate']}={json.dumps(f['value'])}" for f in context))
        say()
        seconds = [r["metrics"].get("seconds", 0) for r in model_runs if r.get("metrics")]
        result = {
            "status": "PASS",
            "semantic_model_used": True,
            "model": getattr(provider, "model", None),
            "model_runs": len(model_runs),
            "model_tasks": sorted({r["task"] for r in model_runs if "task" in r}),
            "model_seconds_total": round(sum(seconds), 1),
            "model_failures": sum(r["status"] in {"FAILED", "PENDING"} for r in model_runs),
            "states": states,
            "decisions": len(store.records("decisions")),
            "proposed_facts_from_extraction": len(proposed),
            "database": str(output_dir / "oracle.db"),
            "scope": "Real bridge/state-machine workflow with simulated agents, scripted human answers, and live local model reasoning",
        }
        (output_dir / "steps.json").write_text(json.dumps(steps, indent=2, default=str))
        (output_dir / "model-runs.json").write_text(json.dumps(model_runs, indent=2, default=str))
        (output_dir / "result.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        for bridge in bridges.values():
            bridge.close()
        store.close()


async def acknowledge_all(bridges, decision):
    for bridge in bridges.values():
        if bridge.session_id in decision["affected_sessions"]:
            await bridge.poll()
            await bridge.emit(EventType.DECISION_ACK, {"decision_id": decision["decision_id"]})
