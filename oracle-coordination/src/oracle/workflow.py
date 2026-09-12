"""Bounded mediation, authoritative decisions, and acknowledgement-driven resume."""

import json
from datetime import datetime, timezone

from .policy import OracleError
from .protocol import (
    AUTHORITATIVE,
    AUTHORITY_ORDER,
    Authority,
    Decision,
    EventType,
    KnowledgeFact,
    Lifecycle,
    Visibility,
    uid,
)
from .reasoning import enqueue
from .store import dump


class Workflow:
    def propose(self, principal, conflict_id, value=None, reason=None, *, use_suggestion=False):
        with self.store.transaction():
            conflict = self.store.record("conflicts", conflict_id)
            if not conflict:
                raise OracleError("NOT_FOUND", "Conflict not found")
            self.policy.owner(principal, conflict["project_id"])
            if use_suggestion:
                suggestion = conflict.get("suggested_proposal")
                if not suggestion:
                    raise OracleError("NOT_FOUND", "No validated model suggestion exists for this conflict yet")
                value = suggestion["value"] if value is None else value
                reason = reason or suggestion["reason"]
            if reason is None:
                raise OracleError("INVALID_INPUT", "A proposal needs a reason")
            return self._propose(conflict, value, reason, principal)

    def _propose(self, conflict, value, reason, actor):
        if conflict["state"] not in {"PAUSED", "HUMAN_ESCALATION"}:
            raise OracleError("INVALID_STATE", "All affected sessions must pause before a proposal")
        rules = self.store.record("projects", conflict["project_id"])["rules"]
        if conflict["round"] >= rules["negotiation_rounds"]:
            raise OracleError("NEGOTIATION_LIMIT", "A human decision is required after the round limit")
        conflict.update(
            round=conflict["round"] + 1,
            responses={},
            state="NEGOTIATION",
            proposal={"value": value, "reason": reason[:3000], "actor": actor},
        )
        self.store.save("conflicts", conflict)
        self.store.audit(actor, "mediation_proposed", conflict["conflict_id"], {**conflict["proposal"], "round": conflict["round"], "subject": conflict["subject"], "predicate": conflict["predicate"]})
        for sid in conflict["affected_sessions"]:
            session = self.store.record("sessions", sid)
            visible = [
                f
                for fid in conflict["fact_ids"]
                if (f := self.store.record("facts", fid))
                and self.policy.visible(f, session["human_id"], session["project_id"])
            ]
            # Human proposals explicitly publish a shared contract. Model output retains its evidence scope.
            allowed = actor != "oracle-model" or len(visible) == len(conflict["fact_ids"])
            payload = {"conflict_id": conflict["conflict_id"], "round": conflict["round"]}
            if allowed:
                payload.update(
                    subject=conflict["subject"],
                    predicate=conflict["predicate"],
                    value=value,
                    instruction="Review the proposed contract and report your position.",
                )
            else:
                payload["instruction"] = (
                    "An owner must publish the contract needed for this negotiation. Request more context."
                )
            self._command(sid, EventType.MEDIATION_PROPOSAL, payload)
        return {
            "conflict_id": conflict["conflict_id"],
            "state": conflict["state"],
            "round": conflict["round"],
        }

    def _respond(self, session, response):
        conflict = self.store.record("conflicts", response.conflict_id)
        if not conflict or session["session_id"] not in conflict["affected_sessions"]:
            raise OracleError("FORBIDDEN", "Conflict access denied")
        if conflict["state"] != "NEGOTIATION":
            raise OracleError("INVALID_STATE", "No active negotiation round")
        if session["session_id"] in conflict["responses"]:
            raise OracleError("NEGOTIATION_LIMIT", "One response per participant per round")
        if len(dump(response).encode()) > 700:
            raise OracleError(
                "NEGOTIATION_LIMIT", "Response exceeds the conservative 700-byte message budget"
            )
        conflict["responses"][session["session_id"]] = response.model_dump(mode="json")
        self.store.audit(
            session["human_id"],
            "negotiation_response",
            conflict["conflict_id"],
            {"round": conflict["round"], "response": response.model_dump(mode="json")},
        )
        for peer in conflict["affected_sessions"]:
            if peer != session["session_id"]:
                # Raw peer rationale may contain private prompts; only disposition is distributed.
                self._command(
                    peer,
                    EventType.NEGOTIATION_MESSAGE,
                    {
                        "conflict_id": conflict["conflict_id"],
                        "round": conflict["round"],
                        "position": response.position,
                        "summary": "A participant responded; Oracle retains the detailed rationale.",
                    },
                )
        if len(conflict["responses"]) == len(conflict["affected_sessions"]):
            accepted = all(r["position"] == "ACCEPT" for r in conflict["responses"].values())
            rules = self.store.record("projects", conflict["project_id"])["rules"]
            if (
                accepted
                and rules.get("oracle_arbitration")
                and conflict["subject"] in rules.get("low_risk_subjects", [])
            ):
                self.store.save("conflicts", conflict)
                return self._arbitrate(conflict)
            conflict["state"] = (
                "HUMAN_ESCALATION"
                if accepted or conflict["round"] >= rules["negotiation_rounds"]
                else "PAUSED"
            )
            self.store.audit("oracle", "round_closed", conflict["conflict_id"], {"round": conflict["round"], "accepted": accepted, "state": conflict["state"]})
            if conflict["state"] == "PAUSED":
                # The round failed with rounds remaining: revise the proposal using the responses.
                enqueue(self.store, session["session_id"], conflict["subject"], "mediation-agent", conflict_id=conflict["conflict_id"])
        self.store.save("conflicts", conflict)
        return {"state": conflict["state"], "round": conflict["round"]}

    def _arbitrate(self, conflict):
        value = conflict["proposal"]["value"]
        active = [
            f
            for f in self.store.active_facts(conflict["subject"], conflict["predicate"])
            if self.policy.related_projects(conflict["project_id"], f["project_id"], conflict["subject"])
        ]
        reason = None
        if any(f["source"] in AUTHORITATIVE and dump(f["value"]) != dump(value) for f in active):
            reason = "A higher-authority contract prevents Oracle arbitration"
        for sid in conflict["affected_sessions"]:
            s = self.store.record("sessions", sid)
            if any(not self.policy.visible(f, s["human_id"], s["project_id"]) for f in active):
                reason = "An owner must authorize contract disclosure"
        if reason:
            conflict["state"] = "HUMAN_ESCALATION"
            self.store.save("conflicts", conflict)
            self.store.audit("oracle", "arbitration_declined", conflict["conflict_id"], {"reason": reason})
            return {"state": "HUMAN_ESCALATION", "reason": reason}
        self.store.audit("oracle", "arbitration", conflict["conflict_id"], {"value": value, "subject": conflict["subject"], "predicate": conflict["predicate"]})
        return self._decide(
            "oracle",
            conflict["project_id"],
            conflict["subject"],
            conflict["predicate"],
            value,
            "Bounded agreement on an owner-approved low-risk subject.",
            Authority.ORACLE_ARBITRATION,
            Visibility.DEPENDENCY_CONSUMERS,
            conflict_id=conflict["conflict_id"],
        )

    def decide(
        self,
        principal,
        project,
        subject,
        predicate,
        value,
        reason,
        *,
        question_id=None,
        conflict_id=None,
        visibility=Visibility.DEPENDENCY_CONSUMERS,
        source=Authority.HUMAN_DECISION,
    ):
        self.policy.owner(principal, project)
        if source not in {Authority.HUMAN_DECISION, Authority.PROJECT_SPEC, Authority.ARCHITECTURE_DECISION}:
            raise OracleError("AUTHORITY_VIOLATION", "Unsupported human decision source")
        with self.store.transaction():
            return self._decide(
                principal,
                project,
                subject,
                predicate,
                value,
                reason,
                source,
                Visibility(visibility),
                question_id=question_id,
                conflict_id=conflict_id,
            )

    def _decide(
        self,
        principal,
        project,
        subject,
        predicate,
        value,
        reason,
        authority,
        visibility,
        *,
        question_id=None,
        conflict_id=None,
    ):
        question = self.store.record("questions", question_id) if question_id else None
        conflict = self.store.record("conflicts", conflict_id) if conflict_id else None
        for record, supplied in [(question, question_id), (conflict, conflict_id)]:
            if supplied and (
                not record
                or record["project_id"] != project
                or record["subject"] != subject
                or record["predicate"] != predicate
            ):
                raise OracleError("INVALID_RESOLUTION", "Resolution must match the referenced issue")
            if record and record["decision_id"]:
                raise OracleError("INVALID_STATE", "Issue already has a decision; create a correction")
        if conflict and not set(conflict["affected_sessions"]).issubset(conflict["pause_acks"]):
            raise OracleError("INVALID_STATE", "Affected sessions have not all acknowledged pause")
        active = [
            f
            for f in self.store.active_facts(subject, predicate)
            if self.policy.related_projects(project, f["project_id"], subject)
        ]
        for fact in active:
            if (
                fact["source"] in AUTHORITATIVE
                and AUTHORITY_ORDER.index(Authority(fact["source"])) < AUTHORITY_ORDER.index(authority)
                and dump(fact["value"]) != dump(value)
            ):
                raise OracleError("AUTHORITY_VIOLATION", "Cannot override a higher-authority source")
            if (
                fact["project_id"] != project
                and fact["source"] in AUTHORITATIVE
                and dump(fact["value"]) != dump(value)
            ):
                raise OracleError(
                    "AUTHORITY_VIOLATION", "The other project's owner must resolve its contract"
                )
        participants = sorted(
            set((question or conflict or {}).get("affected_sessions", []))
            | {s["session_id"] for s in self._related_sessions({"project_id": project, "subject": subject})}
        )
        # Each session's own prior position, captured before supersession, drives its specific instruction.
        prior = {}
        for fact in active:
            if fact.get("session_id") and fact["source"] not in AUTHORITATIVE:
                prior[fact["session_id"]] = fact["value"]
        consumers = [
            r["consumer"]
            for r in self.store.rows(
                "SELECT consumer FROM shares WHERE provider=? AND subject=?", (project, subject)
            )
        ]
        decision = Decision(
            decision_id=uid("d"),
            project_id=project,
            subject=subject,
            predicate=predicate,
            value=value,
            authority=authority,
            author_id=principal,
            reason=reason[:3000],
            visibility=visibility,
            consumer_projects=consumers,
            affected_sessions=participants,
            question_id=question_id,
            conflict_id=conflict_id,
        )
        fact = KnowledgeFact(
            subject=subject,
            predicate=predicate,
            value=value,
            source=authority,
            source_id=decision.decision_id,
            confidence=1.0 if authority == Authority.HUMAN_DECISION else 0.95,
            visibility=visibility,
            consumer_projects=consumers,
            evidence=[decision.decision_id],
            fact_id=uid("fact"),
            project_id=project,
            owner_id=principal,
            message_id=decision.decision_id,
            supersedes=[f["fact_id"] for f in active if f["project_id"] == project],
        )
        for previous in active:
            if previous["project_id"] == project:
                previous["lifecycle"] = Lifecycle.SUPERSEDED
                self.store.save("facts", previous)
        decision.fact_id = fact.fact_id
        self.store.save("facts", fact)
        self.store.save("decisions", decision)
        if question:
            question.update(status="ANSWERED", decision_id=decision.decision_id)
            self.store.save("questions", question)
        if conflict:
            conflict.update(state="RESOLUTION_PENDING", decision_id=decision.decision_id)
            self.store.save("conflicts", conflict)
        self.store.audit(principal, "decision_recorded", decision.decision_id, decision)
        for sid in participants:
            session = self.store.record("sessions", sid)
            if not self.policy.visible(
                fact.model_dump(mode="json"), session["human_id"], session["project_id"]
            ):
                self._command(
                    sid,
                    EventType.CLARIFICATION_REQUEST,
                    {
                        "decision_id": decision.decision_id,
                        "reason": "An owner must publish the contract needed by this session.",
                    },
                )
                continue
            roles = sorted(
                r["predicate"]
                for r in self.store.rows(
                    "SELECT predicate FROM relationships WHERE subject=? AND object=?", (sid, subject)
                )
            )
            previous = prior.get(sid)
            changed = sid in prior and dump(previous) != dump(value)
            session["work_state"] = "RESOLUTION_PENDING"
            self.store.save("sessions", session)
            self._command(
                sid,
                EventType.FINAL_DECISION,
                {
                    "decision_id": decision.decision_id,
                    "subject": subject,
                    "predicate": predicate,
                    "value": value,
                    "authority": authority,
                    "roles": roles,
                    "previous_value": previous,
                    "changed": changed,
                    "instruction": propagation_instruction(
                        decision.decision_id, subject, predicate, value, roles, previous, changed
                    ),
                },
            )
        return {
            "decision_id": decision.decision_id,
            "fact_id": fact.fact_id,
            "affected_sessions": participants,
        }

    def _decision_ack(self, session, decision_id):
        decision = self.store.record("decisions", decision_id)
        if not decision or session["session_id"] not in decision["affected_sessions"]:
            raise OracleError("FORBIDDEN", "Decision acknowledgement denied")
        fact = self.store.record("facts", decision["fact_id"])
        if not self.policy.visible(fact, session["human_id"], session["project_id"]):
            raise OracleError("FORBIDDEN", "Cannot acknowledge an undisclosed decision")
        decision["acknowledgements"] = sorted(set(decision["acknowledgements"] + [session["session_id"]]))
        self.store.save("decisions", decision)
        if set(decision["acknowledgements"]) >= set(decision["affected_sessions"]):
            if decision["conflict_id"]:
                conflict = self.store.record("conflicts", decision["conflict_id"])
                conflict["state"] = "CLOSED"
                self.store.save("conflicts", conflict)
            for f in self.store.active_facts(decision["subject"], decision["predicate"]):
                if f["session_id"] in decision["affected_sessions"] and f["source"] not in AUTHORITATIVE:
                    f["lifecycle"] = Lifecycle.SUPERSEDED
                    self.store.save("facts", f)
            for participant in decision["affected_sessions"]:
                self._maybe_resume(participant)
        return {
            "status": self.store.record("sessions", session["session_id"])["work_state"],
            "decision_id": decision_id,
        }

    def _maybe_resume(self, sid):
        session = self.store.record("sessions", sid)
        if session["connectivity"] == "CLOSED":
            return
        if any(
            sid in q["affected_sessions"] and q["status"] == "OPEN" for q in self.store.records("questions")
        ):
            return
        if any(
            sid in c["affected_sessions"] and c["state"] != "CLOSED" for c in self.store.records("conflicts")
        ):
            return
        decisions = [d for d in self.store.records("decisions") if sid in d["affected_sessions"]]
        if any(not set(d["affected_sessions"]).issubset(d["acknowledgements"]) for d in decisions):
            return
        if session["work_state"] != "RUNNING":
            session["work_state"] = "RUNNING"
            self.store.save("sessions", session)
            self._command(
                sid,
                EventType.RESUME,
                {
                    "decision_ids": [d["decision_id"] for d in decisions],
                    "instruction": "All required resolutions acknowledged. Resume against current context.",
                },
            )

    def _reviewable(self, fact, principal, project):
        """Visible through the reviewing project, or through another project this principal owns."""
        if self.policy.visible(fact, principal, project):
            return True
        return self.policy.role(principal, fact["project_id"]) in {"project_owner", "administrator"} and self.policy.visible(
            fact, principal, fact["project_id"]
        )

    def question_view(self, principal, question_id):
        """Everything an authorized owner needs to answer: positions, related contracts, model suggestion."""
        question = self.store.record("questions", question_id)
        if not question:
            raise OracleError("NOT_FOUND", "Question not found")
        project = question["project_id"]
        self.policy.owner(principal, project)
        facts = [f for fid in question["fact_ids"] if (f := self.store.record("facts", fid))]
        visible = [f for f in facts if self._reviewable(f, principal, project)]
        grouped = {}
        for fact in visible:
            session = self.store.record("sessions", fact["session_id"]) if fact.get("session_id") else None
            holder = {"project": fact["project_id"], "source": fact["source"], "confidence": fact["confidence"]}
            if session and session["project_id"] == project:
                holder["human"] = session["human_id"]
                holder["repository"] = session["repository"]
            grouped.setdefault(dump(fact["value"]), {"value": fact["value"], "held_by": []})["held_by"].append(holder)
        affected = []
        for sid in question["affected_sessions"]:
            session = self.store.record("sessions", sid)
            if session:
                item = {"project": session["project_id"], "work_state": session["work_state"]}
                if session["project_id"] == project:
                    item["human"] = session["human_id"]
                affected.append(item)
        suggestion = question.get("suggestion")
        if suggestion and not set(suggestion.get("evidence_fact_ids", [])).issubset({f["fact_id"] for f in visible}):
            suggestion = None  # Evidence the owner cannot see must not leak through a model paraphrase.
        return {
            "question_id": question_id,
            "project_id": project,
            "subject": question["subject"],
            "predicate": question["predicate"],
            "question": question["question"],
            "status": question["status"],
            "decision_id": question["decision_id"],
            "positions": list(grouped.values()),
            "restricted_evidence_count": len(facts) - len(visible),
            "affected": affected,
            "suggestion": suggestion,
            "assessment": question.get("assessment"),
            "related_contracts": [
                {k: f[k] for k in ["subject", "predicate", "value", "source"]}
                for f in self.store.active_facts(question["subject"])
                if f["predicate"] != question["predicate"]
                and f["source"] in AUTHORITATIVE
                and self.policy.visible(f, principal, project)
            ][:20],
        }

    def curate(self, principal, project):
        """Deterministic knowledge curation (section 63): retire orphaned assumptions, report duplicates and stalls."""
        self.policy.owner(principal, project)
        report = {"project_id": project, "deprecated": [], "reinforced": [], "stale_sessions": [], "stalled": [], "open_questions": 0}
        heartbeat = self.store.record("projects", project)["rules"]["heartbeat_seconds"]
        current = datetime.now(timezone.utc)
        with self.store.transaction():
            sessions = {s["session_id"]: s for s in self.store.records("sessions", project)}
            for sid, session in sessions.items():
                age = (current - datetime.fromisoformat(session["last_seen"])).total_seconds()
                if session["connectivity"] != "CLOSED" and age > heartbeat * 5:
                    report["stale_sessions"].append({"session_id": sid, "human_id": session["human_id"], "seconds_since_seen": int(age)})
            by_value = {}
            for fact in self.store.records("facts", project):
                if fact["lifecycle"] not in {"ACTIVE", "PROPOSED"} or fact["source"] in AUTHORITATIVE:
                    continue
                session = sessions.get(fact["session_id"]) if fact.get("session_id") else None
                if session and session["connectivity"] == "CLOSED":
                    fact["lifecycle"] = Lifecycle.DEPRECATED
                    self.store.save("facts", fact)
                    report["deprecated"].append(fact["fact_id"])
                    continue
                key = (fact["subject"], fact["predicate"], dump(fact["value"]))
                by_value.setdefault(key, []).append(fact["fact_id"])
            for (subject, predicate, value), ids in by_value.items():
                if len(ids) > 1:
                    report["reinforced"].append({"subject": subject, "predicate": predicate, "value": json.loads(value), "fact_ids": ids})
            for conflict in self.store.records("conflicts", project):
                if conflict["state"] not in {"CLOSED", "PAUSED", "NEGOTIATION"} and not conflict["decision_id"]:
                    missing = sorted(set(conflict["affected_sessions"]) - set(conflict["pause_acks"]))
                    if missing:
                        report["stalled"].append({"conflict_id": conflict["conflict_id"], "state": conflict["state"], "awaiting_pause_ack": len(missing)})
            report["open_questions"] = sum(q["status"] == "OPEN" for q in self.store.records("questions", project))
            self.store.audit(principal, "knowledge_curated", project, {k: len(v) if isinstance(v, list) else v for k, v in report.items() if k != "project_id"})
        return report

    def status(self, principal, project):
        self.policy.member(principal, project)
        current = datetime.now(timezone.utc)
        heartbeat = self.store.record("projects", project)["rules"]["heartbeat_seconds"]
        sessions = []
        for s in self.store.records("sessions", project):
            age = (current - datetime.fromisoformat(s["last_seen"])).total_seconds()
            if s["connectivity"] != "CLOSED":
                s["connectivity"] = (
                    "DISCONNECTED" if age > heartbeat * 5 else "STALE" if age > heartbeat * 2 else "ONLINE"
                )
            sessions.append(
                {
                    k: s[k]
                    for k in [
                        "session_id",
                        "human_id",
                        "repository",
                        "agent_type",
                        "work_state",
                        "connectivity",
                        "last_seen",
                    ]
                }
            )
        return {
            "project_id": project,
            "sessions": sessions,
            "open_questions": sum(q["status"] == "OPEN" for q in self.store.records("questions", project)),
            "open_conflicts": sum(c["state"] != "CLOSED" for c in self.store.records("conflicts", project)),
            "decisions": len(self.store.records("decisions", project)),
        }

    def review(self, principal, project, table):
        self.policy.owner(principal, project)
        if table not in {"questions", "conflicts", "decisions", "facts"}:
            raise OracleError("INVALID_QUERY", "Unsupported collection")
        output = []
        for record in self.store.records(table, project):
            if table in {"facts", "decisions"}:
                fact = record if table == "facts" else self.store.record("facts", record["fact_id"])
                if self.policy.visible(fact, principal, project):
                    output.append(record)
                continue
            safe = {
                k: v
                for k, v in record.items()
                if k not in {"fact_ids", "affected_sessions", "responses", "pause_acks", "suggestion", "suggested_proposal", "assessment"}
            }
            safe["facts"] = [
                {k: f[k] for k in ["fact_id", "subject", "predicate", "value", "source", "confidence"]}
                for fid in record["fact_ids"]
                if (f := self.store.record("facts", fid)) and self._reviewable(f, principal, project)
            ]
            visible_ids = {f["fact_id"] for f in safe["facts"]}
            safe["restricted_evidence_count"] = len(record["fact_ids"]) - len(safe["facts"])
            safe["affected_session_count"] = len(record["affected_sessions"])
            if table == "conflicts":
                safe["pause_acks_received"] = len(record["pause_acks"])
                safe["responses"] = {"received": len(record["responses"]), "positions": sorted(r["position"] for r in record["responses"].values())}
            # Model output is shown only when every fact it cites is already visible to this reviewer.
            for key in ["suggestion", "suggested_proposal", "assessment"]:
                model_output = record.get(key)
                if model_output and set(model_output.get("evidence_fact_ids", [])).issubset(visible_ids):
                    safe[key] = model_output
            output.append(safe)
        return output

    def gate(self, session, payload):
        target = str(payload.get("target", ""))
        if not target:
            raise OracleError("INVALID_GATE", "Gate requires a target")
        for c in self.store.records("conflicts"):
            if session["session_id"] in c["affected_sessions"] and c["state"] != "CLOSED":
                return {"status": "PAUSE", "conflict_id": c["conflict_id"]}
        for q in self.store.records("questions"):
            if session["session_id"] in q["affected_sessions"] and q["status"] == "OPEN":
                return {"status": "CLARIFICATION_REQUIRED", "question_id": q["question_id"]}
        if session["work_state"] != "RUNNING":
            return {"status": "PAUSE", "reason": "Resolution acknowledgement pending"}
        authorization = {
            "action": payload.get("action", "modify_contract"),
            "proposed_change": payload.get("proposed_change"),
            "revision": payload.get("revision"),
        }
        for f in self.store.active_facts(target, "change_authorization"):
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(f["timestamp"])).total_seconds()
            if (
                f["source"] == Authority.HUMAN_DECISION
                and f["value"] == authorization
                and 0 <= age < 3600
                and self.policy.visible(f, session["human_id"], session["project_id"])
            ):
                return {
                    "status": "ALLOW",
                    "authorization_fact_id": f["fact_id"],
                    "expires_in_seconds": int(3600 - age),
                }
        participants = [
            s["session_id"]
            for s in self._related_sessions({"project_id": session["project_id"], "subject": target})
        ] or [session["session_id"]]
        q = self._question(
            session,
            target,
            "change_authorization",
            "Approve exactly this shared-contract change: " + dump(authorization),
            [],
            participants,
        )
        return {"status": "CLARIFICATION_REQUIRED", "proposed_authorization": authorization, **q}


def propagation_instruction(decision_id, subject, predicate, value, roles, previous, changed):
    """Participant-specific consequence text (section 46). Only the recipient's own prior position is named."""
    contract = f"{subject}.{predicate} = {dump(value)}"
    producer = bool({"PRODUCES", "IMPLEMENTS"} & set(roles))
    if previous is not None and not changed:
        return (
            f"Decision {decision_id} confirms {contract}, matching your current position. "
            "Continue the current implementation; acknowledge to resume."
        )
    if changed:
        action = (
            "Change your implementation and contract tests to produce exactly this"
            if producer
            else "Update your consumer expectations, types, and tests to expect exactly this"
        )
        return (
            f"Decision {decision_id} sets {contract}. Your session previously used {dump(previous)}. "
            f"{action}; acknowledge before resuming."
        )
    scope = "produced" if producer else "consumed"
    return (
        f"Decision {decision_id} defines {contract}. Apply it wherever {subject} is {scope} in your component; "
        "acknowledge before resuming."
    )
