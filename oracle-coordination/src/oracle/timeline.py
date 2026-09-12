"""Replay the audit ledger as a human-readable, lane-oriented timeline with derived world state.

Read-only over the SQLite file, like the dashboard. Steps follow audit seq, which is the authoritative
order (causes are written before effects), so a PAUSE command always appears after the question or
conflict that triggered it. Every step carries the cumulative state so a UI can scrub through history.
"""

import json
import sqlite3
from pathlib import Path

from .store import dump

VALUE_CHARS = 60
TITLE_CHARS = 120
ACTOR_LANES = {"oracle": "oracle", "oracle-model": "model"}
SETUP_ACTIONS = {
    "project_created",
    "permission_granted",
    "contract_shared",
    "project_rules_imported",
    "participant_enrolled",
}
EVENT_KINDS = {
    "SESSION_REGISTER": "register",
    "CHECKPOINT": "checkpoint",
    "HEARTBEAT": "heartbeat",
    "PAUSE_ACK": "pause_ack",
    "MEDIATION_RESPONSE": "response",
    "NEGOTIATION_MESSAGE": "response",
    "DECISION_ACK": "decision_ack",
    "RESUME_REQUEST": "resume_request",
    "GATE_REQUEST": "gate",
    "SESSION_CLOSE": "other",
}
REPORT_EVENTS = {"ASSUMPTION_UPDATE", "DECISION_UPDATE", "INTERFACE_UPDATE", "QUESTION_UPDATE", "TASK_UPDATE"}
CLASSIFICATION_TEXT = {
    "CONSISTENT": "No competing value exists for this contract among related sessions.",
    "UNDERSPECIFIED": "Agent assumptions differ and no authoritative source settles them; a clarification "
    "question is opened and affected sessions are asked to pause at a safe checkpoint.",
    "CONFLICTING": "Declared contracts cannot all hold simultaneously; a conflict is confirmed and "
    "affected sessions are asked to pause.",
    "IRRELEVANT": "Values differ but only one session is involved, so nothing is coordinated.",
    "STALE": "The report is older than the last accepted state and was ignored.",
}


def short(value, limit=VALUE_CHARS):
    """JSON form of a value (strings keep their quotes), truncated for one-line titles."""
    text = dump(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clip(text, limit=TITLE_CHARS):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def contract(subject, predicate, value, *, around=0):
    """`subject.predicate = value`; the value shrinks first when the surrounding title text is long."""
    budget = max(24, min(VALUE_CHARS, TITLE_CHARS - around - len(f"{subject}.{predicate} = ")))
    return f"{subject}.{predicate} = {short(value, budget)}"


def build_timeline(db_path) -> dict:
    path = Path(db_path)
    if not path.exists():
        return {"status": "waiting", "steps": [], "participants": []}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        sessions = {r["id"]: json.loads(r["data"]) for r in db.execute("SELECT id,data FROM sessions ORDER BY rowid")}
        events = {
            r["message_id"]: (json.loads(r["data"]), json.loads(r["response"]) if r["response"] else {})
            for r in db.execute("SELECT message_id,data,response FROM events")
        }
        commands = {r["id"]: json.loads(r["data"]) for r in db.execute("SELECT id,data FROM messages")}
        audit = [
            dict(r) for r in db.execute("SELECT seq,actor,action,object_id,data,timestamp FROM audit ORDER BY seq")
        ]
    finally:
        db.close()
    builder = _Builder(sessions, events, commands)
    steps = [step for row in audit if (step := builder.step(row))]
    return {
        "status": "ok",
        "database": str(path),
        "participants": builder.participants(audit),
        "steps": steps,
    }


class _Builder:
    def __init__(self, sessions, events, commands):
        self.sessions, self.events, self.commands = sessions, events, commands
        self.state = {"sessions": {}, "questions": [], "conflicts": {}, "decisions": 0, "model_runs": 0}
        self.decided_conflicts = {}  # conflict_id -> {"affected": [...], "resumed": set()}

    # -- lanes -------------------------------------------------------------------------------------------
    def human(self, session_id):
        session = self.sessions.get(session_id or "")
        return session["human_id"] if session else None

    def participants(self, audit):
        humans, owners = [], []
        with_sessions = {s["human_id"] for s in self.sessions.values()}
        for row in audit:
            actor = row["actor"]
            if actor in ACTOR_LANES:
                continue
            bucket = humans if actor in with_sessions else owners
            if actor not in bucket:
                bucket.append(actor)
        for session in self.sessions.values():
            if session["human_id"] not in humans:
                humans.append(session["human_id"])
        project = {}
        for session in self.sessions.values():
            project.setdefault(session["human_id"], session["project_id"])
        return (
            [{"lane": h, "kind": "human", "project": project.get(h)} for h in humans]
            + [{"lane": o, "kind": "owner", "project": None} for o in owners]
            + [{"lane": "oracle", "kind": "oracle", "project": None}, {"lane": "model", "kind": "model", "project": None}]
        )

    # -- state -------------------------------------------------------------------------------------------
    def set_work_state(self, session_id, value, only_if=None):
        human = self.human(session_id)
        if not human:
            return
        session = self.sessions[session_id]
        current = self.state["sessions"].get(human, {"project": session["project_id"], "work_state": "RUNNING"})
        if only_if is None or current["work_state"] in only_if:
            current = {**current, "work_state": value}
        self.state["sessions"][human] = current

    def snapshot(self):
        return {
            "sessions": {h: dict(s) for h, s in self.state["sessions"].items()},
            "open_questions": list(self.state["questions"]),
            "open_conflicts": list(self.state["conflicts"]),
            "decisions": self.state["decisions"],
            "model_runs": self.state["model_runs"],
        }

    def resume(self, session_id):
        self.set_work_state(session_id, "RUNNING")
        for conflict_id, pending in list(self.decided_conflicts.items()):
            if session_id in pending["affected"]:
                pending["resumed"].add(session_id)
            live = {
                sid for sid in pending["affected"] if self.sessions.get(sid, {}).get("connectivity") != "CLOSED"
            }
            if live <= pending["resumed"]:
                self.state["conflicts"].pop(conflict_id, None)
                self.decided_conflicts.pop(conflict_id)

    # -- steps -------------------------------------------------------------------------------------------
    def step(self, row):
        action, actor = row["action"], row["actor"]
        if action == "negotiation_response":
            return None
        data = json.loads(row["data"]) if row["data"] else {}
        base = {
            "seq": row["seq"],
            "timestamp": row["timestamp"],
            "kind": "other",
            "lane": ACTOR_LANES.get(actor, actor),
            "target": None,
            "title": f"{actor} {action}",
            "detail": "",
            "payload": data,
            "refs": {},
        }
        if action in EVENT_KINDS or action in REPORT_EVENTS:
            self.event_step(base, row, data)
        elif action == "delivery_queued":
            self.command_step(base, row, data)
        elif action == "clarification_opened":
            self.state["questions"].append(row["object_id"])
            base.update(
                kind="question_opened",
                title=f"Oracle opened question {row['object_id']} on {data.get('subject')}.{data.get('predicate')}",
                detail=(
                    f"{len(data.get('fact_ids', []))} competing fact(s) from {len(data.get('affected_sessions', []))} "
                    f"session(s): {', '.join(self.humans(data.get('affected_sessions', [])))}.\n"
                    "Affected sessions will be asked to pause until an authorized human answers."
                ),
                refs={"question_id": row["object_id"]},
            )
        elif action == "conflict_confirmed":
            self.state["conflicts"][row["object_id"]] = list(data.get("affected_sessions", []))
            base.update(
                kind="conflict_confirmed",
                title=f"Oracle confirmed {data.get('type')} conflict {row['object_id']} on "
                f"{data.get('subject')}.{data.get('predicate')}",
                detail=(
                    f"{len(data.get('fact_ids', []))} incompatible fact(s) across "
                    f"{', '.join(self.humans(data.get('affected_sessions', [])))}.\n"
                    "Each affected session is asked to pause; mediation starts once all have acknowledged."
                ),
                refs={"conflict_id": row["object_id"]},
            )
        elif action.startswith("semantic_"):
            self.model_step(base, row, data)
        elif action == "reasoning_failed":
            base.update(
                kind="model_failed",
                lane="model",
                title=f"model {data.get('task')} failed on {data.get('subject')}: {data.get('error')}",
                detail="The job will be retried once; deterministic coordination never waited on it.",
                refs={"job_id": row["object_id"]},
            )
        elif action == "mediation_proposed":
            base.update(
                kind="proposal",
                title=f"{base['lane']} proposed {contract(data.get('subject'), data.get('predicate'), data.get('value'))} "
                f"(round {data.get('round')})",
                detail=str(data.get("reason", "")),
                refs={"conflict_id": row["object_id"]},
            )
        elif action == "round_closed":
            outcome = "all participants accepted" if data.get("accepted") else "not accepted"
            base.update(
                kind="round_closed",
                title=f"Oracle closed round {data.get('round')} of {row['object_id']}: {outcome} → {data.get('state')}",
                detail=(
                    "A human decision is now required." if data.get("state") == "HUMAN_ESCALATION"
                    else "Rounds remain; the mediation agent will revise the proposal."
                ),
                refs={"conflict_id": row["object_id"]},
            )
        elif action == "arbitration":
            base.update(
                kind="arbitration",
                title=f"Oracle arbitrated {contract(data.get('subject'), data.get('predicate'), data.get('value'))}",
                detail="Owner-allowlisted low-risk subject with unanimous acceptance; Oracle records the decision itself.",
                refs={"conflict_id": row["object_id"]},
            )
        elif action == "arbitration_declined":
            base.update(
                kind="arbitration",
                title=f"Oracle declined to arbitrate {row['object_id']}: {data.get('reason')}",
                detail="The conflict escalates to a human owner.",
                refs={"conflict_id": row["object_id"]},
            )
        elif action == "decision_recorded":
            self.decision_step(base, row, data)
        elif action in SETUP_ACTIONS:
            base.update(kind="setup", title=self.setup_title(actor, action, row["object_id"], data))
        elif action == "knowledge_curated":
            base.update(
                kind="curate",
                title=f"{actor} curated knowledge in {row['object_id']}: {data.get('deprecated', 0)} deprecated, "
                f"{data.get('reinforced', 0)} reinforced, {data.get('stalled', 0)} stalled",
            )
        elif action == "overlap_advisory":
            overlaps = data.get("overlaps", [])
            human = self.human(row["object_id"])
            base.update(
                kind="overlap",
                target=human,
                title=f"Oracle → {human}: {len(overlaps)} file/symbol overlap(s) with other sessions",
                detail="\n".join(
                    f"{o['object']} also touched by {', '.join(s['human_id'] for s in o['sessions'])}" for o in overlaps[:6]
                ),
                refs={"session_id": row["object_id"]},
            )
        base["title"] = clip(base["title"])
        base["state"] = self.snapshot()
        return base

    def humans(self, session_ids):
        return [self.human(sid) or sid for sid in session_ids]

    def setup_title(self, actor, action, object_id, data):
        if action == "project_created":
            return f"{actor} created project {object_id}"
        if action == "permission_granted":
            return f"{actor} granted {data.get('role')} on {object_id} to {data.get('human')}"
        if action == "contract_shared":
            return f"{actor} shared {object_id} from {data.get('provider')} to {data.get('consumer')}"
        if action == "participant_enrolled":
            return f"{actor} enrolled {object_id} as {data.get('role')} on {data.get('project')} (restricted SSH key)"
        return f"{actor} imported project rules into {object_id}"

    def event_step(self, base, row, data):
        action, actor = row["action"], row["actor"]
        envelope, response = self.events.get(row["object_id"], ({}, {}))
        payload = envelope.get("payload", {})
        session_id = envelope.get("session_id") or data.get("session_id")
        if action == "SESSION_REGISTER":
            session_id = response.get("oracle_session_id")
        base.update(
            kind=EVENT_KINDS.get(action, "report"),
            payload={"envelope": envelope, "response": response},
            refs={"message_id": row["object_id"], **({"session_id": session_id} if session_id else {})},
        )
        if action == "SESSION_REGISTER":
            self.set_work_state(session_id, "RUNNING", only_if=())
            base["title"] = (
                f"{actor} registered a {payload.get('agent_type')} session in {payload.get('project_id')} "
                f"(repo {payload.get('repository')})"
            )
            interests = payload.get("interests", [])
            base["detail"] = (
                f"Interested in: {', '.join(interests) if interests else 'nothing declared'}.\n"
                f"Oracle returned {len(response.get('relevant_context', []))} visible context fact(s)."
            )
        elif action == "HEARTBEAT":
            base["title"] = f"{actor} heartbeat ({response.get('status')})"
        elif action == "SESSION_CLOSE":
            self.set_work_state(session_id, "CLOSED")
            base["title"] = f"{actor} closed the session"
        elif action == "CHECKPOINT":
            self.checkpoint(base, actor, payload, response)
        elif action == "TASK_UPDATE":
            base["title"] = f"{actor} task: {short(payload.get('task', ''), 90)}"
        elif action == "QUESTION_UPDATE":
            base["title"] = f"{actor} asks: {short(payload.get('question', ''), 90)}"
            base["detail"] = f"Question {response.get('question_id')} opened for the session's own task."
            base["refs"]["question_id"] = response.get("question_id")
        elif action in {"ASSUMPTION_UPDATE", "DECISION_UPDATE"}:
            verb = "assumes" if action == "ASSUMPTION_UPDATE" else "decides"
            base["title"] = f"{actor} {verb} {contract(payload.get('subject'), payload.get('predicate'), payload.get('value'))}"
            self.classify(base, response, payload.get("visibility"))
        elif action == "INTERFACE_UPDATE":
            results = response.get("results", [])
            worst = self.worst(results)
            base["title"] = (
                f"{actor} {payload.get('role')} {payload.get('name')} {short(payload.get('contract', {}))}"
            )
            self.classify(base, worst or {"classification": "CONSISTENT"}, payload.get("visibility"))
        elif action == "PAUSE_ACK":
            self.set_work_state(session_id, "PAUSED")
            ref = "conflict" if payload.get("conflict_id") else "question"
            record_id = payload.get("conflict_id") or payload.get("question_id")
            base["title"] = f"{actor} acknowledged pause for {ref} {record_id}"
            base["detail"] = "The session saved its state at a safe checkpoint and is now PAUSED."
            base["refs"][ref + "_id"] = record_id
        elif action in {"MEDIATION_RESPONSE", "NEGOTIATION_MESSAGE"}:
            # A unanimous last response on an allowlisted subject is answered with the arbitration decision itself.
            outcome = (
                f"(round {response['round']}) → {response.get('state')}"
                if "round" in response
                else f"→ ARBITRATED by Oracle ({response.get('decision_id')})"
                if response.get("decision_id")
                else f"→ {response.get('state') or response.get('status') or 'accepted'}"
            )
            base["title"] = f"{actor} responds {payload.get('position')} on {payload.get('conflict_id')} {outcome}"
            detail = [str(payload.get("reason", ""))]
            if response.get("decision_id"):
                base["refs"]["decision_id"] = response["decision_id"]
            if payload.get("alternative") is not None:
                detail.append(f"Alternative: {short(payload['alternative'])}")
            if payload.get("risks"):
                detail.append("Risks: " + "; ".join(payload["risks"]))
            base["detail"] = "\n".join(d for d in detail if d)
            base["refs"]["conflict_id"] = payload.get("conflict_id")
        elif action == "DECISION_ACK":
            base["title"] = f"{actor} acknowledged decision {payload.get('decision_id')} → {response.get('status')}"
            base["refs"]["decision_id"] = payload.get("decision_id")
        elif action == "RESUME_REQUEST":
            base["title"] = f"{actor} requested resume → {response.get('status')}"
        elif action == "GATE_REQUEST":
            base["title"] = f"{actor} gate on {payload.get('target')} → {response.get('status')}"
            base["detail"] = short(payload.get("proposed_change"), 200) if payload.get("proposed_change") else ""
            for key in ["question_id", "conflict_id"]:
                if response.get(key):
                    base["refs"][key] = response[key]
        else:
            base["title"] = f"{actor} {action}"

    def worst(self, results):
        order = ["CONFLICTING", "UNDERSPECIFIED", "IRRELEVANT", "STALE", "CONSISTENT"]
        ranked = sorted(
            (r for r in results if "classification" in r),
            key=lambda r: order.index(r["classification"]) if r["classification"] in order else len(order),
        )
        return ranked[0] if ranked else None

    def classify(self, base, response, visibility=None):
        classification = response.get("classification")
        if not classification:
            return
        suffix = f" → {classification}"
        if response.get("conflict_type"):
            suffix += f" ({response['conflict_type']})"
        base["title"] += suffix
        lines = [CLASSIFICATION_TEXT.get(classification, "")]
        if visibility:
            lines.insert(0, f"Visibility {visibility}.")
        if response.get("deduplicated"):
            lines.append("Identical to this session's earlier report; no new fact recorded.")
        for key in ["question_id", "conflict_id"]:
            if response.get(key):
                base["refs"][key] = response[key]
                lines.append(f"Linked {key.replace('_', ' ')}: {response[key]}.")
        base["detail"] = "\n".join(line for line in lines if line)

    def checkpoint(self, base, actor, packet, response):
        if response.get("classification") == "STALE":
            base["title"] = f"{actor} checkpoint ignored as STALE"
            base["detail"] = CLASSIFICATION_TEXT["STALE"]
            return
        files = packet.get("files", {})
        symbols = packet.get("symbols", {})
        n_files = sum(len(files.get(k, [])) for k in ["modified", "planned", "read"])
        n_symbols = sum(len(symbols.get(k, [])) for k in ["created", "modified", "removed", "read"])
        queued = any(
            packet.get(k) for k in ["human_intent", "agent_interpretation", "implementation_plan", "current_implementation", "summary"]
        )
        reported = sum(len(packet.get(k, [])) for k in ["assumptions", "decisions", "schemas", "interfaces", "questions"])
        base["title"] = (
            f"{actor} checkpoint {short(packet.get('task', ''), 50)} ({n_files} files, {n_symbols} symbols, "
            f"{reported} reports){' → queued model extraction' if queued else ''}"
        )
        results = [r for r in response.get("results", []) if "classification" in r]
        lines = []
        for classification in ["CONFLICTING", "UNDERSPECIFIED", "CONSISTENT", "IRRELEVANT"]:
            count = sum(r["classification"] == classification for r in results)
            if count:
                lines.append(f"{count} report(s) classified {classification}.")
        if response.get("overlaps"):
            lines.append(f"{len(response['overlaps'])} file/symbol overlap(s) with other sessions.")
        if queued:
            lines.append("Narrative fields present: knowledge-extractor job queued for the model.")
        base["detail"] = "\n".join(lines)
        for key in ["question_id", "conflict_id"]:
            if found := next((r[key] for r in results if r.get(key)), None):
                base["refs"][key] = found

    def command_step(self, base, row, data):
        command = self.commands.get(row["object_id"], {"type": data.get("type"), "session_id": data.get("session_id"), "payload": {}})
        payload = command.get("payload", {})
        session_id, kind = command.get("session_id"), command.get("type")
        human = self.human(session_id) or session_id
        base.update(kind="command", lane="oracle", target=self.human(session_id), payload=command)
        base["refs"] = {"session_id": session_id, "message_id": row["object_id"]}
        for key in ["question_id", "conflict_id", "decision_id"]:
            if payload.get(key):
                base["refs"][key] = payload[key]
        head = f"Oracle → {human}: {kind}"
        if kind == "PAUSE":
            self.set_work_state(session_id, "PAUSE_REQUESTED", only_if={"RUNNING"})
            ref = "question" if payload.get("question_id") else "conflict"
            base["title"] = f"{head} ({payload.get('mode', '').replace('_', ' ')}) for {ref} {payload.get(ref + '_id')}"
            base["detail"] = str(payload.get("reason", ""))
        elif kind == "FINAL_DECISION":
            self.set_work_state(session_id, "RESOLUTION_PENDING")
            base["title"] = f"{head} " + contract(
                payload.get("subject"), payload.get("predicate"), payload.get("value"), around=len(head) + 1
            )
            base["detail"] = str(payload.get("instruction", ""))
        elif kind == "RESUME":
            self.resume(session_id)
            base["title"] = f"{head} ({len(payload.get('decision_ids', []))} decision(s) acknowledged)"
            base["detail"] = str(payload.get("instruction", ""))
        elif kind == "MEDIATION_PROPOSAL":
            if "value" in payload:
                base["title"] = f"{head} round {payload.get('round')}: " + contract(
                    payload.get("subject"), payload.get("predicate"), payload.get("value")
                )
            else:
                base["title"] = f"{head} round {payload.get('round')}: contract withheld pending owner publication"
            base["detail"] = str(payload.get("instruction", ""))
        elif kind == "NEGOTIATION_MESSAGE":
            base["title"] = f"{head} round {payload.get('round')}: peer position {payload.get('position')}"
            base["detail"] = str(payload.get("summary", ""))
        elif kind == "CLARIFICATION_REQUEST":
            base["title"] = f"{head} for decision {payload.get('decision_id')}"
            base["detail"] = str(payload.get("reason", ""))
        else:
            base["title"] = head

    def model_step(self, base, row, data):
        self.state["model_runs"] += 1
        task, output = data.get("task", ""), data.get("output") or {}
        metrics = data.get("metrics") or {}
        name = metrics.get("model") or "model"
        head = f"model ({name}) {task}"
        base.update(kind="model", lane="model", refs={"job_id": row["object_id"], "session_id": data.get("session_id")})
        base["payload"] = {"bundle": data.get("bundle"), "output": output, "metrics": metrics}
        if task == "knowledge-extractor":
            base["title"] = (
                f"{head} extracted {len(output.get('facts', []))} fact(s), {len(output.get('questions', []))} "
                f"question(s) from {self.human(data.get('session_id')) or data.get('session_id')}'s checkpoint"
            )
            base["detail"] = "\n".join(
                contract(f.get("subject"), f.get("predicate"), f.get("value")) for f in output.get("facts", [])[:5]
            )
        elif task == "conflict-detector":
            base["title"] = (
                f"{head}: {output.get('classification')} on {data.get('subject')} "
                f"(confidence {output.get('confidence')}, severity {output.get('severity')})"
            )
            base["detail"] = f"{output.get('summary', '')}\n{output.get('reason', '')}\nRecommended: {output.get('recommended_action')}."
        elif task == "clarification-generator":
            options = output.get("options", [])
            base["title"] = (
                f"{head} drafted {len(options)} option(s) for {data.get('subject')}, "
                f"recommends {short(output.get('recommendation'))}"
            )
            base["detail"] = (
                f"{output.get('question', '')}\nOptions: {', '.join(options)}\n{output.get('recommendation_reason', '')}"
            )
        elif task == "mediation-agent":
            base["title"] = (
                f"{head} proposed {contract(output.get('subject'), output.get('predicate'), output.get('value'))} "
                f"(requires_human={output.get('requires_human')})"
            )
            base["detail"] = str(output.get("reason", ""))
        else:
            base["title"] = f"{head} on {data.get('subject')}"
        seconds = metrics.get("seconds")
        if seconds is not None:
            base["detail"] = (base["detail"] + f"\n{seconds:.1f}s of model time.").strip()

    def decision_step(self, base, row, data):
        self.state["decisions"] += 1
        if data.get("question_id") in self.state["questions"]:
            self.state["questions"].remove(data["question_id"])
        if data.get("conflict_id"):
            affected = self.state["conflicts"].get(data["conflict_id"]) or data.get("affected_sessions", [])
            self.state["conflicts"].setdefault(data["conflict_id"], list(affected))
            self.decided_conflicts[data["conflict_id"]] = {"affected": list(affected), "resumed": set()}
        prefix, suffix = f"{base['lane']} decided ", f" ({data.get('authority')})"
        base.update(
            kind="decision",
            title=prefix
            + contract(data.get("subject"), data.get("predicate"), data.get("value"), around=len(prefix + suffix))
            + suffix,
            detail=(
                f"{data.get('reason', '')}\nVisibility {data.get('visibility')}; "
                f"{len(data.get('affected_sessions', []))} session(s) receive a FINAL_DECISION and must acknowledge."
            ),
            refs={k: data[k] for k in ["decision_id", "question_id", "conflict_id"] if data.get(k)},
        )


def render_text(timeline: dict, *, include_heartbeats=False) -> str:
    if timeline.get("status") != "ok":
        return "waiting for the database to be created"
    lines = []
    steps = [s for s in timeline["steps"] if include_heartbeats or s["kind"] != "heartbeat"]
    width = max((len(s["lane"]) + len(s["target"] or "") for s in steps), default=10) + 3
    for step in steps:
        clock = step["timestamp"][11:19] if len(step["timestamp"]) >= 19 else step["timestamp"]
        who = f"{step['lane']} → {step['target']}" if step["target"] else step["lane"]
        lines.append(f"{step['seq']:03d} {clock}  {who:<{width}} {step['title']}")
        for detail in step["detail"].splitlines():
            if detail.strip():
                lines.append("      " + detail)
    last = steps[-1]["state"] if steps else {"model_runs": 0, "decisions": 0}
    lines.append(f"-- {len(steps)} steps, {last['model_runs']} model runs, {last['decisions']} decisions")
    return "\n".join(lines)
