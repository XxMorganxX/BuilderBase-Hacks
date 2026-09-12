"""Leased model work with validated evidence. Model output never gains authority or disclosure scope.

Each skill is a (bundle, validate, apply) triple. Bundles are built inside a transaction from the OKB,
the model runs outside any transaction, and the validated result is applied in a second transaction that
re-checks the record is still in the state the job was queued for.
"""

import json
from datetime import datetime, timedelta, timezone

from .model import ModelFailure
from .protocol import (
    Authority,
    Classification,
    ClarificationRequest,
    ConflictAssessment,
    KnowledgeFact,
    KnowledgeUpdate,
    Lifecycle,
    MediationProposal,
    Visibility,
    now,
    uid,
)
from .store import dump

BUNDLE_FACT_BYTES = 16000
MAX_ATTEMPTS = 2
LEASE_MINUTES = 4


def relevant_facts(oracle, session_id, subject):
    session = oracle.store.record("sessions", session_id)
    return [
        f
        for f in oracle.store.active_facts(subject)
        if oracle.policy.related_projects(session["project_id"], f["project_id"], subject)
    ]


def compact(fact, *, with_project=False):
    keys = ["fact_id", "subject", "predicate", "value", "source", "confidence", "lifecycle"]
    item = {k: fact[k] for k in keys}
    if with_project:
        item["project_id"] = fact["project_id"]
    return item


def select_facts(facts, **kwargs):
    facts = sorted(facts, key=lambda f: (f["source"] not in {"human_decision", "project_spec"}, f["timestamp"]))
    selected, size = [], 0
    for fact in facts:
        item = compact(fact, **kwargs)
        length = len(dump(item).encode())
        if size + length <= BUNDLE_FACT_BYTES:
            selected.append(item)
            size += length
    return selected, len(selected) != len(facts)


def bundle_for(oracle, session_id, subject):
    selected, incomplete = select_facts(relevant_facts(oracle, session_id, subject))
    return {
        "subject": subject,
        "facts": selected,
        "incomplete": incomplete,
        "instruction": "Assess compatibility; competing assumptions without authority imply underspecification.",
    }


def positions(oracle, facts):
    """Which project holds which value. Project identifiers are Oracle-internal, not developer prompts."""
    result = []
    for fact in facts:
        session = oracle.store.record("sessions", fact["session_id"]) if fact.get("session_id") else None
        result.append(
            {
                "fact_id": fact["fact_id"],
                "project_id": fact["project_id"],
                "value": fact["value"],
                "source": fact["source"],
                "role": sorted(
                    r["predicate"]
                    for r in oracle.store.rows(
                        "SELECT predicate FROM relationships WHERE subject=? AND object=?",
                        (session["session_id"] if session else "", fact["subject"]),
                    )
                ),
            }
        )
    return result


def record_facts(oracle, record):
    return [f for fid in record["fact_ids"] if (f := oracle.store.record("facts", fid))]


def other_contract_facts(oracle, project, subject, predicate):
    """Authoritative context on the same subject but other predicates, e.g. an approved field list."""
    return [
        f
        for f in oracle.store.active_facts(subject)
        if f["predicate"] != predicate
        and f["source"] in {"human_decision", "project_spec", "architecture_decision", "public_contract"}
        and oracle.policy.related_projects(project, f["project_id"], subject)
    ]


class Skill:
    schema = None
    task = ""

    def bundle(self, worker, row, metadata):
        raise NotImplementedError

    def validate(self, answer, bundle):
        ids = {f["fact_id"] for f in bundle.get("facts", [])}
        cited = getattr(answer, "evidence_fact_ids", None)
        if cited is not None and not set(cited).issubset(ids):
            raise ModelFailure("Model cited nonexistent evidence")

    def apply(self, worker, row, metadata, bundle, answer):
        raise NotImplementedError


class KnowledgeExtractor(Skill):
    schema, task = KnowledgeUpdate, "knowledge-extractor"

    def bundle(self, worker, row, metadata):
        checkpoint = worker.oracle.store.one("SELECT data FROM checkpoints WHERE id=?", (metadata["checkpoint_id"],))
        packet = json.loads(checkpoint["data"])
        keys = ["task", "human_intent", "agent_interpretation", "implementation_plan", "current_implementation", "summary"]
        return {k: packet.get(k, "") for k in keys}

    def apply(self, worker, row, metadata, bundle, answer):
        store = worker.oracle.store
        session = store.record("sessions", row["session_id"])
        for item in answer.facts:
            # The model cannot grant itself authority, publication scope, or active status.
            existing = [
                f
                for f in store.rows(
                    "SELECT data FROM facts WHERE subject=? AND predicate=? AND session_id=? AND lifecycle='PROPOSED'",
                    (item.subject, item.predicate, row["session_id"]),
                )
                if dump(json.loads(f["data"])["value"]) == dump(item.value)
            ]
            if existing:
                continue
            fact = KnowledgeFact(
                subject=item.subject,
                predicate=item.predicate,
                value=item.value,
                source=Authority.ORACLE_INFERENCE,
                source_id=metadata["checkpoint_id"],
                confidence=min(item.confidence, 0.7),
                visibility=Visibility.OWNER_ONLY,
                lifecycle=Lifecycle.PROPOSED,
                evidence=[metadata["checkpoint_id"]],
                fact_id=uid("fact"),
                project_id=session["project_id"],
                owner_id=session["human_id"],
                session_id=row["session_id"],
                message_id=row["id"],
            )
            store.save("facts", fact)
        return "COMPLETE"


class ConflictDetector(Skill):
    schema, task = ConflictAssessment, "conflict-detector"

    def bundle(self, worker, row, metadata):
        bundle = bundle_for(worker.oracle, row["session_id"], row["subject"])
        metadata["revision"] = sorted(f["fact_id"] for f in relevant_facts(worker.oracle, row["session_id"], row["subject"]))
        return bundle

    def validate(self, answer, bundle):
        super().validate(answer, bundle)
        if answer.classification in {Classification.CONFLICTING, Classification.UNDERSPECIFIED} and not answer.evidence_fact_ids:
            raise ModelFailure("Consequential assessments require evidence")
        if bundle["incomplete"] and answer.classification == Classification.CONSISTENT:
            raise ModelFailure("Incomplete evidence cannot establish consistency")

    def apply(self, worker, row, metadata, bundle, answer):
        oracle, store = worker.oracle, worker.oracle.store
        current = sorted(f["fact_id"] for f in relevant_facts(oracle, row["session_id"], row["subject"]))
        if current != metadata["revision"]:
            return "STALE"
        session = store.record("sessions", row["session_id"])
        assessment = {**answer.model_dump(mode="json"), "job_id": row["id"], "model": worker.provider.last_metrics.get("model")}
        # Annotate the open issue on this contract so the authorized human sees the semantic reading,
        # including a NO-CONFLICT reading of a deterministic mismatch (specification section 83).
        annotated = False
        for table, key, open_state in [("conflicts", "state", lambda r: r["state"] != "CLOSED"), ("questions", "status", lambda r: r["status"] == "OPEN")]:
            for record in store.records(table):
                if (
                    open_state(record)
                    and record["subject"] == row["subject"]
                    and oracle.policy.related_projects(record["project_id"], session["project_id"], row["subject"])
                ):
                    record["assessment"] = assessment
                    store.save(table, record)
                    annotated = True
        if (
            not annotated
            and answer.classification in {Classification.CONFLICTING, Classification.UNDERSPECIFIED}
            and answer.confidence >= 0.8
        ):
            facts = [store.record("facts", fid) for fid in answer.evidence_fact_ids]
            participants = sorted({s["session_id"] for s in oracle._related_sessions(facts[0])})
            if len(participants) > 1:
                oracle._question(
                    session,
                    row["subject"],
                    "semantic_review",
                    "Review a possible semantic incompatibility in this shared contract.",
                    answer.evidence_fact_ids,
                    participants,
                )
        return "COMPLETE"


class ClarificationGenerator(Skill):
    schema, task = ClarificationRequest, "clarification-generator"

    def bundle(self, worker, row, metadata):
        oracle = worker.oracle
        question = oracle.store.record("questions", metadata["question_id"])
        facts = record_facts(oracle, question)
        selected, incomplete = select_facts(facts, with_project=True)
        return {
            "subject": question["subject"],
            "predicate": question["predicate"],
            "current_question": question["question"],
            "facts": selected,
            "positions": positions(oracle, facts),
            "related_contracts": [compact(f) for f in other_contract_facts(oracle, question["project_id"], question["subject"], question["predicate"])][:20],
            "incomplete": incomplete,
            "instruction": "Write the smallest human question that resolves this missing specification, with quick-select options and a recommendation.",
        }

    def validate(self, answer, bundle):
        super().validate(answer, bundle)
        if not answer.options:
            raise ModelFailure("A clarification needs quick-select options")
        if answer.recommendation and answer.recommendation not in answer.options:
            raise ModelFailure("Recommendation must be one of the offered options")

    def apply(self, worker, row, metadata, bundle, answer):
        store = worker.oracle.store
        question = store.record("questions", metadata["question_id"])
        if not question or question["status"] != "OPEN":
            return "STALE"
        question["suggestion"] = {
            **answer.model_dump(mode="json"),
            "job_id": row["id"],
            "model": worker.provider.last_metrics.get("model"),
        }
        store.save("questions", question)
        return "COMPLETE"


class MediationAgent(Skill):
    schema, task = MediationProposal, "mediation-agent"

    def bundle(self, worker, row, metadata):
        oracle = worker.oracle
        conflict = oracle.store.record("conflicts", metadata["conflict_id"])
        facts = record_facts(oracle, conflict)
        selected, incomplete = select_facts(facts, with_project=True)
        rules = oracle.store.record("projects", conflict["project_id"])["rules"]
        return {
            "subject": conflict["subject"],
            "predicate": conflict["predicate"],
            "conflict_type": conflict["type"],
            "summary": conflict["summary"],
            "round": conflict["round"],
            "previous_proposal": conflict.get("proposal"),
            "previous_responses": [
                {"position": r["position"], "reason": r["reason"], "alternative": r.get("alternative")}
                for r in conflict.get("responses", {}).values()
            ],
            "facts": selected,
            "positions": positions(oracle, facts),
            "related_contracts": [compact(f) for f in other_contract_facts(oracle, conflict["project_id"], conflict["subject"], conflict["predicate"])][:20],
            "low_risk_subject": conflict["subject"] in rules.get("low_risk_subjects", []),
            "incomplete": incomplete,
            "instruction": "Propose one neutral resolution for exactly this contract. Mark requires_human for security, public API, data, or product consequences.",
        }

    def validate(self, answer, bundle):
        super().validate(answer, bundle)
        if not answer.evidence_fact_ids:
            raise ModelFailure("A proposal must cite the conflicting evidence")
        if answer.subject != bundle["subject"] or answer.predicate != bundle["predicate"]:
            raise ModelFailure("Proposal must address the conflict's contract")

    def apply(self, worker, row, metadata, bundle, answer):
        oracle, store = worker.oracle, worker.oracle.store
        conflict = store.record("conflicts", metadata["conflict_id"])
        if not conflict or conflict["state"] != "PAUSED" or conflict["decision_id"]:
            return "STALE"
        conflict["suggested_proposal"] = {
            **answer.model_dump(mode="json"),
            "job_id": row["id"],
            "model": worker.provider.last_metrics.get("model"),
        }
        store.save("conflicts", conflict)
        rules = store.record("projects", conflict["project_id"])["rules"]
        if (
            rules.get("oracle_arbitration")
            and conflict["subject"] in rules.get("low_risk_subjects", [])
            and not answer.requires_human
            and conflict["round"] < rules["negotiation_rounds"]
        ):
            # Owner-allowlisted low-risk subject: Oracle opens the bounded round itself.
            oracle._propose(conflict, answer.value, answer.reason, "oracle-model")
        return "COMPLETE"


SKILLS = {skill.task: skill for skill in [KnowledgeExtractor(), ConflictDetector(), ClarificationGenerator(), MediationAgent()]}


def enqueue(store, session_id, subject, task, **metadata):
    """Queue durable model work. Deterministic coordination never waits on it."""
    store.db.execute(
        "INSERT INTO reasoning_jobs(id,session_id,subject,state,data) VALUES(?,?,?,?,?)",
        (uid("job"), session_id, subject, "PENDING", dump({"task": task, **metadata})),
    )


class ReasoningWorker:
    def __init__(self, oracle, provider):
        self.oracle, self.provider = oracle, provider

    def _fail(self, row, error):
        store = self.oracle.store
        with store.transaction():
            state = "FAILED" if row["attempts"] + 1 >= MAX_ATTEMPTS else "PENDING"
            store.db.execute(
                "UPDATE reasoning_jobs SET state=?,result=? WHERE id=?",
                (state, dump({"error": str(error)}), row["id"]),
            )
            store.audit("oracle-model", "reasoning_failed", row["id"], {"error": str(error), "task": json.loads(row["data"]).get("task"), "subject": row["subject"]})
        return {"status": state, "job_id": row["id"], "error": str(error)}

    async def once(self):
        store = self.oracle.store
        with store.transaction():
            row = store.one(
                "SELECT * FROM reasoning_jobs WHERE state='PENDING' OR (state='RUNNING' AND lease_until<?) "
                "ORDER BY rowid LIMIT 1",
                (now(),),
            )
            if not row:
                return {"status": "IDLE"}
            if row["attempts"] >= MAX_ATTEMPTS:
                store.db.execute(
                    "UPDATE reasoning_jobs SET state='FAILED',result=? WHERE id=?",
                    (dump({"error": "Attempt limit exhausted after interrupted execution"}), row["id"]),
                )
                return {"status": "FAILED", "job_id": row["id"]}
            deadline = (datetime.now(timezone.utc) + timedelta(minutes=LEASE_MINUTES)).isoformat()
            store.db.execute(
                "UPDATE reasoning_jobs SET state='RUNNING',attempts=attempts+1,lease_until=? WHERE id=?",
                (deadline, row["id"]),
            )
            metadata = json.loads(row["data"])
            skill = SKILLS.get(metadata.get("task", "conflict-detector"))
            if skill is None:
                return self._fail(row, "Unknown reasoning task")
            try:
                bundle = skill.bundle(self, row, metadata)
            except (KeyError, TypeError, ValueError) as error:
                return self._fail(row, f"Bundle construction failed: {type(error).__name__}")
        try:
            answer = await self.provider.structured_completion(skill.task, bundle, skill.schema)
            skill.validate(answer, bundle)
        except ModelFailure as error:
            return self._fail(row, error)
        with store.transaction():
            result = {
                "task": skill.task,
                "subject": row["subject"],
                "session_id": row["session_id"],
                "bundle": bundle,
                "output": answer.model_dump(mode="json"),
                "metrics": self.provider.last_metrics,
            }
            # Ledger first, then effects: replay shows the model's evidence and answer before anything it triggered.
            store.audit("oracle-model", "semantic_" + skill.task.replace("-", "_"), row["id"], result)
            state = skill.apply(self, row, metadata, bundle, answer)
            store.db.execute("UPDATE reasoning_jobs SET state=?,result=? WHERE id=?", (state, dump({**result, "state": state}), row["id"]))
        return {"status": state, "job_id": row["id"], **{k: v for k, v in result.items() if k != "bundle"}}

    async def drain(self, limit=50):
        """Process queued jobs until idle; used by scenarios and tests."""
        results = []
        for _ in range(limit):
            result = await self.once()
            if result["status"] == "IDLE":
                break
            results.append(result)
        return results
