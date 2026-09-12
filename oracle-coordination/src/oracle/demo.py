"""Repeatable three-repository coordination demo, using the same public bridge operations."""

import json
from pathlib import Path
import subprocess

from .bridge import Bridge
from .protocol import EventType, Registration
from .service import Oracle
from .store import Store
from .transport import LocalTransport


async def run_demo(output_dir: Path, transport_factory=None):
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (output_dir / "oracle.db").exists():
        raise ValueError("Choose a new demo directory; existing history will not be overwritten")
    store = Store(output_dir / "oracle.db")
    oracle = Oracle(store)
    bridges = {}
    transcript = []
    people = [("alice", "backend"), ("bob", "web"), ("carol", "analytics")]
    try:
        for human, project in people:
            oracle.create_project(
                project,
                "owner",
                rules={"oracle_arbitration": True, "low_risk_subjects": ["InternalEncoding"]},
            )
            oracle.grant("owner", project, human)
        for subject in ["User.id", "AuthenticationResponse", "InternalEncoding"]:
            oracle.share("owner", "backend", "web", subject)
            oracle.share("owner", "backend", "analytics", subject)
        for human, project in people:
            repository = output_dir / project
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            transport = (
                transport_factory(human, output_dir / "oracle.db")
                if transport_factory
                else LocalTransport(oracle, human)
            )
            bridge = Bridge(output_dir / (human + "-bridge.db"), transport, human, project, repository)
            interests = ["User.id"] + (
                ["AuthenticationResponse", "InternalEncoding"] if human != "carol" else []
            )
            await bridge.register(
                Registration(
                    project_id=project,
                    repository=project,
                    human_id=human,
                    machine_id=human + "-simulated-machine",
                    agent_type="generic-demo",
                    agent_session_id=human + "-demo",
                    interests=interests,
                )
            )
            bridges[human] = bridge
        for human, value in [("alice", "UUID"), ("bob", "integer"), ("carol", "email")]:
            result = await bridges[human].emit(
                EventType.ASSUMPTION_UPDATE,
                {"subject": "User.id", "predicate": "type", "value": value, "visibility": "PUBLIC_CONTRACT"},
            )
            transcript.append({"step": "identity_assumption", "human": human, "result": result})
        question = store.records("questions")[0]
        for bridge in bridges.values():
            await bridge.poll()
            await bridge.emit(EventType.PAUSE_ACK, {"question_id": question["question_id"]})
        decision = oracle.decide(
            "owner",
            "backend",
            "User.id",
            "type",
            "UUID",
            "Human chooses canonical UUID identity",
            question_id=question["question_id"],
        )
        await acknowledge_all(bridges, decision)
        transcript.append({"step": "human_clarification", "decision": decision})
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
        for human in ["alice", "bob"]:
            await bridges[human].emit(EventType.PAUSE_ACK, {"conflict_id": conflict["conflict_id"]})
        oracle.propose(
            "owner",
            conflict["conflict_id"],
            "JWT bearer",
            "Human proposes the shared authentication mechanism",
        )
        for human in ["alice", "bob"]:
            await bridges[human].emit(
                EventType.MEDIATION_RESPONSE,
                {
                    "conflict_id": conflict["conflict_id"],
                    "position": "ACCEPT",
                    "reason": "Compatible with my component",
                },
            )
        assert store.record("conflicts", conflict["conflict_id"])["state"] == "HUMAN_ESCALATION"
        decision = oracle.decide(
            "owner",
            "backend",
            "AuthenticationResponse",
            "authentication",
            "JWT bearer",
            "Security-sensitive authentication choice approved by project owner",
            conflict_id=conflict["conflict_id"],
        )
        await acknowledge_all(bridges, decision)
        fields = {"access_token": "string", "expires_at": "UTC timestamp", "user_id": "UUID"}
        contract = oracle.decide(
            "owner",
            "backend",
            "AuthenticationResponse",
            "fields",
            fields,
            "Owner approves the complete login response",
        )
        await acknowledge_all(bridges, contract)
        transcript.append({"step": "authentication_mediation", "decision": decision, "contract": contract})
        for human, value in [("alice", "UTF-8"), ("bob", "utf8")]:
            await bridges[human].emit(
                EventType.DECISION_UPDATE,
                {
                    "subject": "InternalEncoding",
                    "predicate": "name",
                    "value": value,
                    "visibility": "PUBLIC_CONTRACT",
                },
            )
        conflict = next(c for c in store.records("conflicts") if c["subject"] == "InternalEncoding")
        for human in ["alice", "bob"]:
            await bridges[human].emit(EventType.PAUSE_ACK, {"conflict_id": conflict["conflict_id"]})
        oracle.propose(
            "owner", conflict["conflict_id"], "UTF-8", "Equivalent internal encoding normalization"
        )
        for human in ["alice", "bob"]:
            result = await bridges[human].emit(
                EventType.MEDIATION_RESPONSE,
                {
                    "conflict_id": conflict["conflict_id"],
                    "position": "ACCEPT",
                    "reason": "Equivalent spelling",
                },
            )
        decision = store.record("decisions", result["decision_id"])
        assert decision["authority"] == "oracle_arbitration"
        await acknowledge_all(bridges, decision)
        transcript.append({"step": "low_risk_oracle_arbitration", "decision_id": decision["decision_id"]})
        for human, project in people:
            await bridges[human].poll()
            (output_dir / (human + "-context.json")).write_text(
                json.dumps(await bridges[human].context(), indent=2)
            )
        states = {
            human: store.record("sessions", bridge.session_id)["work_state"]
            for human, bridge in bridges.items()
        }
        assert set(states.values()) == {"RUNNING"}
        assert all(c["state"] == "CLOSED" for c in store.records("conflicts"))
        result = {
            "status": "PASS",
            "transport": "SSH" if transport_factory else "local",
            "simulated_people": 3,
            "repositories": 3,
            "states": states,
            "decisions": len(store.records("decisions")),
            "database": str(output_dir / "oracle.db"),
            "semantic_model_used": False,
            "scope": "Real bridge/state-machine workflow with simulated agents and scripted human answers",
        }
        (output_dir / "transcript.json").write_text(json.dumps(transcript, indent=2, default=str))
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
