"""Authoritative coordination state machine; model work runs outside transactions."""

import hashlib
import json
from datetime import datetime, timezone

from .policy import OracleError, Policy
from .protocol import (
    AUTHORITATIVE,
    AUTHORITY_ORDER,
    Authority,
    Classification,
    Conflict,
    ConflictType,
    ContextPacket,
    Envelope,
    EventType,
    FactInput,
    InterfaceReport,
    KnowledgeFact,
    Lifecycle,
    NegotiationResponse,
    OracleCommand,
    Question,
    Registration,
    Session,
    now,
    uid,
)
from .reasoning import enqueue
from .store import Store, dump
from .workflow import Workflow


class Oracle(Workflow):
    def __init__(self, store: Store):
        self.store = store
        self.policy = Policy(store)

    def create_project(self, project_id, owner, *, team="default", organization="default", rules=None):
        """Host-administrator bootstrap; deliberately not exposed through the participant RPC."""
        value = {
            "project_id": project_id,
            "organization_id": organization,
            "team_id": team,
            "owner_id": owner,
            "rules": {
                "heartbeat_seconds": 60,
                "negotiation_rounds": 3,
                "strict_coordination": True,
                "oracle_arbitration": False,
                **(rules or {}),
            },
        }
        with self.store.transaction():
            self.store.db.execute("INSERT INTO projects(id,data) VALUES(?,?)", (project_id, dump(value)))
            self.store.db.execute(
                "INSERT INTO permissions VALUES(?,?,?)", (project_id, owner, "project_owner")
            )
            self.store.audit(owner, "project_created", project_id, value)
        return value

    def grant(self, actor, project, human, role="developer"):
        self.policy.owner(actor, project)
        if role not in {"developer", "component_owner", "project_owner"}:
            raise OracleError("INVALID_ROLE", "Unsupported role")
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO permissions VALUES(?,?,?) ON CONFLICT(project_id,human_id) "
                "DO UPDATE SET role=excluded.role",
                (project, human, role),
            )
            self.store.audit(actor, "permission_granted", project, {"human": human, "role": role})

    def share(self, actor, provider, consumer, subject):
        self.policy.owner(actor, provider)
        p, c = self.store.record("projects", provider), self.store.record("projects", consumer)
        if not c or p["organization_id"] != c["organization_id"]:
            raise OracleError("FORBIDDEN", "Sharing requires projects in the same organization")
        with self.store.transaction():
            self.store.db.execute("INSERT OR IGNORE INTO shares VALUES(?,?,?)", (provider, consumer, subject))
            self.store.audit(actor, "contract_shared", subject, {"provider": provider, "consumer": consumer})

    def ingest(self, principal: str, event: Envelope | dict):
        event = Envelope.model_validate(event)
        self.policy.member(principal, event.project_id)
        if event.sender != principal or event.recipient != "oracle":
            raise OracleError("FORBIDDEN", "Envelope identity does not match authenticated principal")
        if len(event.model_dump_json().encode()) > 128_000:
            raise OracleError("TOO_LARGE", "Context packet exceeds 128 KB")
        fingerprint = hashlib.sha256(dump(event).encode()).hexdigest()
        with self.store.transaction():
            previous = self.store.one("SELECT * FROM events WHERE message_id=?", (event.message_id,))
            if previous:
                if previous["principal"] != principal or previous["request_hash"] != fingerprint:
                    raise OracleError(
                        "IDEMPOTENCY_CONFLICT", "Message ID was already used for another request"
                    )
                return json.loads(previous["response"])
            self.store.audit(
                principal,
                event.type.value,
                event.message_id,
                {"session_id": event.session_id, "project_id": event.project_id},
            )
            if event.type == EventType.SESSION_REGISTER:
                result = self._register(principal, event)
            else:
                session = self.policy.session(principal, event.session_id, event.project_id)
                if session["connectivity"] == "CLOSED":
                    raise OracleError("SESSION_CLOSED", "Register a new session to continue")
                session["last_seen"] = now()
                session["connectivity"] = "ONLINE"
                self.store.save("sessions", session)
                result = self._event(principal, session, event)
            self.store.db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?)",
                (event.message_id, principal, fingerprint, dump(event), dump(result), now()),
            )
            return result

    def _register(self, principal, event):
        value = Registration.model_validate(event.payload)
        if value.human_id != principal or value.project_id != event.project_id:
            raise OracleError("FORBIDDEN", "Registration identity does not match authentication")
        keys = (value.project_id, principal, value.machine_id, value.agent_session_id)
        existing = self.store.one(
            "SELECT session_id FROM registrations WHERE project_id=? AND human_id=? "
            "AND machine_id=? AND agent_session_id=?",
            keys,
        )
        if existing:
            session = self.store.record("sessions", existing["session_id"])
            if session["connectivity"] == "CLOSED":
                raise OracleError("SESSION_CLOSED", "Use a new agent_session_id")
        else:
            session = Session(session_id=uid("session"), **value.model_dump()).model_dump(mode="json")
            self.store.save("sessions", session)
            self.store.db.execute(
                "INSERT INTO registrations VALUES(?,?,?,?,?)", (*keys, session["session_id"])
            )
            for name in value.interests:
                self._relationship(value.project_id, session["session_id"], "USES", name, event.message_id)
        return {
            "oracle_session_id": session["session_id"],
            "status": "registered",
            "relevant_context": self.context(principal, session["session_id"]),
        }

    def _event(self, principal, session, event):
        kind, payload = event.type, event.payload
        if kind == EventType.HEARTBEAT:
            return {"status": session["work_state"], "reasoning_invoked": False}
        if kind == EventType.SESSION_CLOSE:
            session["connectivity"] = "CLOSED"
            self.store.save("sessions", session)
            return {"status": "CLOSED"}
        if kind == EventType.CHECKPOINT:
            return self._checkpoint(session, event)
        if kind == EventType.TASK_UPDATE:
            task = str(payload.get("task", ""))
            if len(task) > 4000:
                raise OracleError("TOO_LARGE", "Task exceeds limit")
            session["task"] = task
            self.store.save("sessions", session)
            return {"status": session["work_state"]}
        if kind in {EventType.ASSUMPTION_UPDATE, EventType.DECISION_UPDATE}:
            item = FactInput.model_validate(payload)
            source = (
                Authority.AGENT_ASSUMPTION
                if kind == EventType.ASSUMPTION_UPDATE
                else Authority.AGENT_DECISION
            )
            return self._record_report(session, item, event, source)
        if kind == EventType.INTERFACE_UPDATE:
            return self._interface(session, InterfaceReport.model_validate(payload), event)
        if kind == EventType.QUESTION_UPDATE:
            return self._question(
                session,
                payload.get("subject", "task"),
                payload.get("predicate", "definition"),
                str(payload["question"]),
                [],
                [session["session_id"]],
            )
        if kind == EventType.GATE_REQUEST:
            return self.gate(session, payload)
        if kind == EventType.PAUSE_ACK:
            return self._pause_ack(session, payload)
        if kind in {EventType.MEDIATION_RESPONSE, EventType.NEGOTIATION_MESSAGE}:
            return self._respond(session, NegotiationResponse.model_validate(payload))
        if kind == EventType.DECISION_ACK:
            return self._decision_ack(session, payload["decision_id"])
        if kind == EventType.RESUME_REQUEST:
            self._maybe_resume(session["session_id"])
            return {"status": self.store.record("sessions", session["session_id"])["work_state"]}
        raise OracleError("UNSUPPORTED_EVENT", "This event is not permitted from a coding session")

    def _relationship(self, project, subject, predicate, obj, source, entity_type="Contract"):
        self.store.db.execute(
            "INSERT OR IGNORE INTO relationships VALUES(?,?,?,?,?)",
            (project, subject, predicate, obj, source),
        )
        self.store.db.execute(
            "INSERT OR IGNORE INTO entities VALUES(?,?,?,?)",
            (project, obj, entity_type, dump({"source_id": source})),
        )

    def _touch(self, session, packet, message_id):
        """Deterministic same-file / same-symbol signals within one project (specification section 31).

        Paths stay project-scoped: sessions in other projects never learn them.
        """
        objects = [("file:" + path, "File") for path in packet.files.modified[:200]]
        objects += [
            ("symbol:" + name, "Symbol")
            for name in (packet.symbols.created + packet.symbols.modified + packet.symbols.removed)[:200]
        ]
        overlaps = []
        for obj, entity_type in objects:
            self._relationship(session["project_id"], session["session_id"], "TOUCHES", obj, message_id, entity_type)
            others = [
                {"session_id": s["session_id"], "human_id": s["human_id"], "repository": s["repository"]}
                for row in self.store.rows(
                    "SELECT s.data FROM sessions s JOIN relationships r ON r.subject=s.id "
                    "WHERE r.object=? AND r.predicate='TOUCHES' AND r.project_id=? AND s.id<>?",
                    (obj, session["project_id"], session["session_id"]),
                )
                if (s := json.loads(row["data"]))["connectivity"] != "CLOSED"
            ]
            if others:
                overlaps.append({"object": obj, "sessions": others})
        if overlaps:
            self.store.audit("oracle", "overlap_advisory", session["session_id"], {"overlaps": overlaps})
        return overlaps

    def _checkpoint(self, session, event):
        packet = ContextPacket.model_validate(event.payload)
        if packet.session_id != session["session_id"] or packet.timestamp.tzinfo is None:
            raise OracleError("INVALID_CHECKPOINT", "Checkpoint session and timezone must be valid")
        timestamp = packet.timestamp.astimezone(timezone.utc)
        if (timestamp - datetime.now(timezone.utc)).total_seconds() > 300:
            raise OracleError("INVALID_CHECKPOINT", "Checkpoint timestamp is too far in the future")
        if session["last_checkpoint_at"] and timestamp <= datetime.fromisoformat(
            session["last_checkpoint_at"]
        ):
            return {"classification": "STALE", "status": session["work_state"]}
        session["last_checkpoint_at"] = timestamp.isoformat()
        session["task"] = packet.task or session["task"]
        self.store.save("sessions", session)
        self.store.db.execute(
            "INSERT INTO checkpoints VALUES(?,?,?)", (event.message_id, session["session_id"], dump(packet))
        )
        if any(
            [
                packet.human_intent,
                packet.agent_interpretation,
                packet.implementation_plan,
                packet.current_implementation,
                packet.summary,
            ]
        ):
            enqueue(
                self.store, session["session_id"], "checkpoint", "knowledge-extractor", checkpoint_id=event.message_id
            )
        overlaps = self._touch(session, packet, event.message_id)
        results = []
        for item in packet.assumptions:
            results.append(self._record_report(session, item, event, Authority.AGENT_ASSUMPTION))
        for item in packet.decisions + packet.schemas:
            results.append(self._record_report(session, item, event, Authority.AGENT_DECISION))
        for item in packet.interfaces:
            results.append(self._interface(session, item, event))
        for name in packet.dependencies:
            self._relationship(
                session["project_id"], session["session_id"], "DEPENDS_ON", name, event.message_id
            )
        for question in packet.questions:
            results.append(
                self._question(session, "task", "definition", question, [], [session["session_id"]])
            )
        return {
            "status": self.store.record("sessions", session["session_id"])["work_state"],
            "results": results,
            "overlaps": overlaps,
        }

    def _record_report(self, session, item, event, source):
        if item.source not in {Authority.AGENT_ASSUMPTION, Authority.AGENT_DECISION}:
            raise OracleError("AUTHORITY_VIOLATION", "Coding agents cannot assert authoritative facts")
        if item.lifecycle != Lifecycle.ACTIVE:
            raise OracleError("AUTHORITY_VIOLATION", "Use a human correction to change fact lifecycle")
        item = item.model_copy(update={"source": source, "source_id": event.message_id})
        self._relationship(
            session["project_id"], session["session_id"], "USES", item.subject, event.message_id
        )
        old = [
            f
            for f in self.store.active_facts(item.subject, item.predicate)
            if f["session_id"] == session["session_id"] and f["source"] == source
        ]
        for fact in old:
            if (
                dump(fact["value"]) == dump(item.value)
                and fact["visibility"] == item.visibility
                and fact["consumer_projects"] == item.consumer_projects
            ):
                return {"fact_id": fact["fact_id"], **self._analyze(fact), "deduplicated": True}
        fact = KnowledgeFact(
            **item.model_dump(),
            fact_id=uid("fact"),
            project_id=session["project_id"],
            owner_id=session["human_id"],
            session_id=session["session_id"],
            message_id=event.message_id,
            supersedes=[f["fact_id"] for f in old],
        )
        for previous in old:
            previous["lifecycle"] = Lifecycle.SUPERSEDED
            self.store.save("facts", previous)
        self.store.save("facts", fact)
        result = self._analyze(fact.model_dump(mode="json"))
        if result.get("variants", 1) > 1:
            # Deterministic signal first (section 31); semantic evaluation only when values actually differ.
            enqueue(self.store, session["session_id"], fact.subject, "conflict-detector", fact_id=fact.fact_id)
        result.pop("variants", None)
        return {"fact_id": fact.fact_id, **result}

    def _interface(self, session, interface, event):
        self._relationship(
            session["project_id"], session["session_id"], interface.role, interface.name, event.message_id
        )
        results = []
        for name, value in interface.contract.items():
            item = FactInput(
                subject=interface.name,
                predicate=name,
                value=value,
                source=Authority.AGENT_DECISION,
                confidence=0.75,
                visibility=interface.visibility,
                consumer_projects=interface.consumer_projects,
                evidence=interface.evidence,
            )
            results.append(self._record_report(session, item, event, Authority.AGENT_DECISION))
        return {"interface": interface.name, "results": results}

    def _related_sessions(self, fact):
        rows = self.store.rows(
            "SELECT DISTINCT s.data FROM sessions s JOIN relationships r ON r.subject=s.id WHERE r.object=?",
            (fact["subject"],),
        )
        return [
            s
            for row in rows
            if (s := json.loads(row["data"]))["connectivity"] != "CLOSED"
            and self.policy.related_projects(fact["project_id"], s["project_id"], fact["subject"])
        ]

    def _analyze(self, fact):
        relevant = [
            f
            for f in self.store.active_facts(fact["subject"], fact["predicate"])
            if self.policy.related_projects(fact["project_id"], f["project_id"], fact["subject"])
        ]
        variants = {dump(f["value"]) for f in relevant}
        sessions = self._related_sessions(fact)
        if len(variants) <= 1:
            return {"classification": Classification.CONSISTENT, "variants": len(variants)}
        authoritative = [f for f in relevant if f["source"] in AUTHORITATIVE]
        implementations = [f for f in relevant if f["source"] == Authority.AGENT_DECISION]
        ids = sorted(
            {s["session_id"] for s in sessions} | {f["session_id"] for f in relevant if f["session_id"]}
        )
        if not authoritative and len({dump(f["value"]) for f in implementations}) < 2:
            if len(ids) < 2:
                return {"classification": Classification.IRRELEVANT, "variants": len(variants)}
            question = self._question(
                sessions[0],
                fact["subject"],
                fact["predicate"],
                "Choose the canonical shared contract; current assumptions differ.",
                [f["fact_id"] for f in relevant],
                ids,
            )
            return {"classification": Classification.UNDERSPECIFIED, "variants": len(variants), **question}
        anchor = min(authoritative or relevant, key=lambda f: AUTHORITY_ORDER.index(Authority(f["source"])))
        conflict = self._conflict(anchor, relevant, ids)
        return {
            "classification": Classification.CONFLICTING,
            "conflict_id": conflict["conflict_id"],
            "conflict_type": conflict["type"],
            "variants": len(variants),
        }

    def _conflict_type(self, anchor, facts):
        """Deterministic typing from the evidence shape; the semantic detector may refine it later."""
        if anchor["source"] in AUTHORITATIVE:
            return ConflictType.SPEC_VIOLATION
        predicate = anchor["predicate"].lower()
        if any(k in predicate for k in ["auth", "security", "permission", "secret", "token", "encrypt"]):
            return ConflictType.SECURITY_CONFLICT
        if predicate in {"version", "versions", "dependency", "dependencies"} or anchor["subject"].startswith("dependency:"):
            return ConflictType.DEPENDENCY_CONFLICT
        roles = {
            r["predicate"]
            for r in self.store.rows("SELECT predicate FROM relationships WHERE object=?", (anchor["subject"],))
        }
        if roles & {"PRODUCES", "CONSUMES", "IMPLEMENTS"}:
            return ConflictType.INTERFACE_MISMATCH
        if predicate in {"type", "fields", "schema", "format", "nullable", "encoding"}:
            return ConflictType.SCHEMA_MISMATCH
        if all(f["source"] == Authority.AGENT_ASSUMPTION for f in facts):
            return ConflictType.ASSUMPTION_CONFLICT
        return ConflictType.IMPLEMENTATION_CONFLICT

    def _question(self, session, subject, predicate, text, facts, participants):
        for q in self.store.records("questions"):
            if (
                q["status"] == "OPEN"
                and q["subject"] == subject
                and q["predicate"] == predicate
                and self.policy.related_projects(q["project_id"], session["project_id"], subject)
            ):
                q["affected_sessions"] = sorted(set(q["affected_sessions"] + participants))
                grown = set(facts) - set(q["fact_ids"])
                q["fact_ids"] = sorted(set(q["fact_ids"] + facts))
                self.store.save("questions", q)
                self._request_pause(participants, "question_id", q["question_id"])
                if grown and q["fact_ids"]:
                    enqueue(self.store, session["session_id"], subject, "clarification-generator", question_id=q["question_id"])
                return {"question_id": q["question_id"]}
        question = Question(
            question_id=uid("q"),
            project_id=session["project_id"],
            subject=subject,
            predicate=predicate,
            question=text[:2000],
            fact_ids=facts,
            affected_sessions=participants,
        )
        self.store.save("questions", question)
        self.store.audit("oracle", "clarification_opened", question.question_id, {"fact_ids": facts, "subject": subject, "predicate": predicate, "affected_sessions": participants})
        self._request_pause(participants, "question_id", question.question_id)
        if facts:
            enqueue(self.store, session["session_id"], subject, "clarification-generator", question_id=question.question_id)
        return {"question_id": question.question_id}

    def _request_pause(self, participants, key, record_id):
        for session_id in participants:
            session = self.store.record("sessions", session_id)
            if not session or session["connectivity"] == "CLOSED":
                continue
            if session["work_state"] == "RUNNING":
                session["work_state"] = "PAUSE_REQUESTED"
                self.store.save("sessions", session)
            exists = any(
                json.loads(row["data"])["payload"].get(key) == record_id
                for row in self.store.rows("SELECT data FROM messages WHERE session_id=?", (session_id,))
            )
            if not exists:
                self._command(
                    session_id,
                    EventType.PAUSE,
                    {
                        key: record_id,
                        "mode": "safe_checkpoint",
                        "reason": "A shared dependency requires coordination. Save state and acknowledge at a safe checkpoint.",
                    },
                )

    def _conflict(self, anchor, facts, participants):
        for c in self.store.records("conflicts"):
            if (
                c["state"] != "CLOSED"
                and c["subject"] == anchor["subject"]
                and c["predicate"] == anchor["predicate"]
                and self.policy.related_projects(c["project_id"], anchor["project_id"], anchor["subject"])
            ):
                if c["decision_id"]:
                    continue
                c["fact_ids"] = sorted(set(c["fact_ids"] + [f["fact_id"] for f in facts]))
                c["affected_sessions"] = sorted(set(c["affected_sessions"] + participants))
                self.store.save("conflicts", c)
                self._request_pause(participants, "conflict_id", c["conflict_id"])
                return c
        conflict = Conflict(
            conflict_id=uid("c"),
            project_id=anchor["project_id"],
            subject=anchor["subject"],
            predicate=anchor["predicate"],
            type=self._conflict_type(anchor, facts),
            summary="Declared contracts cannot all hold simultaneously.",
            fact_ids=[f["fact_id"] for f in facts],
            affected_sessions=participants,
        )
        self.store.save("conflicts", conflict)
        self.store.audit(
            "oracle",
            "conflict_confirmed",
            conflict.conflict_id,
            {"fact_ids": conflict.fact_ids, "subject": conflict.subject, "predicate": conflict.predicate, "type": conflict.type, "affected_sessions": participants},
        )
        self._request_pause(participants, "conflict_id", conflict.conflict_id)
        return conflict.model_dump(mode="json")

    def _command(self, session_id, kind, payload):
        self.store.deliver(OracleCommand(type=kind, session_id=session_id, payload=payload))

    def context(self, principal, session_id, *, subjects=None, max_bytes=6000):
        session = self.policy.session(principal, session_id)
        names = subjects or [
            row["object"]
            for row in self.store.rows("SELECT object FROM relationships WHERE subject=?", (session_id,))
        ]
        result, used = [], 0
        seen = set()
        facts = [f for subject in names[:100] for f in self.store.active_facts(subject)]
        facts.sort(key=lambda f: (AUTHORITY_ORDER.index(Authority(f["source"])), f["timestamp"]))
        for fact in facts:
            if fact["fact_id"] in seen or not self.policy.visible(fact, principal, session["project_id"]):
                continue
            seen.add(fact["fact_id"])
            projected = {
                k: fact[k]
                for k in [
                    "fact_id",
                    "subject",
                    "predicate",
                    "value",
                    "source",
                    "confidence",
                    "lifecycle",
                    "timestamp",
                ]
            }
            # Cross-project contracts intentionally exclude private evidence paths and authors.
            if fact["project_id"] == session["project_id"]:
                projected["evidence"] = fact["evidence"]
            size = len(dump(projected).encode())
            if used + size > max_bytes:
                continue
            result.append(projected)
            used += size
        return result

    def poll(self, principal, session_id, after=0, limit=100):
        self.policy.session(principal, session_id)
        if after < 0 or not 1 <= limit <= 100:
            raise OracleError("INVALID_CURSOR", "Invalid inbox cursor or limit")
        rows = self.store.rows(
            "SELECT seq,data FROM messages WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
            (session_id, after, limit),
        )
        return {
            "messages": [{"cursor": row["seq"], **json.loads(row["data"])} for row in rows],
            "next_cursor": rows[-1]["seq"] if rows else after,
        }

    def acknowledge_delivery(self, principal, session_id, cursor):
        self.policy.session(principal, session_id)
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE messages SET acknowledged_at=? WHERE session_id=? AND seq<=?",
                (now(), session_id, cursor),
            )
        return {"acknowledged_cursor": cursor}

    def _pause_ack(self, session, payload):
        record_id = payload.get("conflict_id") or payload.get("question_id")
        table = "conflicts" if payload.get("conflict_id") else "questions"
        record = self.store.record(table, record_id)
        if not record or session["session_id"] not in record["affected_sessions"]:
            raise OracleError("FORBIDDEN", "Pause does not belong to this session")
        if (
            record.get("decision_id")
            or record.get("state") == "CLOSED"
            or record.get("status") in {"ANSWERED", "CANCELED"}
        ):
            raise OracleError("INVALID_STATE", "This pause has already been resolved")
        session["work_state"] = "PAUSED"
        self.store.save("sessions", session)
        if table == "conflicts":
            record["pause_acks"] = sorted(set(record["pause_acks"] + [session["session_id"]]))
            if set(record["pause_acks"]) >= set(record["affected_sessions"]) and record["state"] != "PAUSED":
                record["state"] = "PAUSED"
                # Everyone is at a safe checkpoint: ask the model for a neutral proposal (section 38).
                enqueue(self.store, session["session_id"], record["subject"], "mediation-agent", conflict_id=record_id)
            self.store.save(table, record)
        return {"status": "PAUSED", "record_id": record_id}
