import json

import pytest

from oracle.bridge import Bridge
from oracle.protocol import EventType, Registration
from oracle.reasoning import ReasoningWorker
from oracle.transport import LocalTransport, TransportUnavailable
from conftest import report


class SwitchTransport(LocalTransport):
    offline = False
    lose_reply = False

    async def send(self, message):
        if self.offline:
            raise TransportUnavailable("Network unavailable")
        result = await super().send(message)
        if self.lose_reply:
            self.lose_reply = False
            raise TransportUnavailable("Reply lost after server commit")
        return result


@pytest.mark.asyncio
async def test_offline_queue_restart_and_lost_ack(ecosystem, tmp_path):
    oracle, _ = ecosystem
    transport = SwitchTransport(oracle, "alice")
    path = tmp_path / "bridge.db"
    bridge = Bridge(path, transport, "alice", "backend")
    registration = Registration(
        project_id="backend",
        repository="backend",
        human_id="alice",
        machine_id="offline",
        agent_type="generic",
        agent_session_id="offline",
    )
    await bridge.register(registration)
    transport.offline = True
    result = await bridge.emit(
        EventType.ASSUMPTION_UPDATE, {"subject": "Local", "predicate": "type", "value": "string"}
    )
    assert result["status"] == "QUEUED_OFFLINE"
    assert (await bridge.gate("modify_contract", "Local", "change"))["allow"] is False
    bridge.close()
    transport.offline = False
    transport.lose_reply = True
    bridge = Bridge(path, transport, "alice", "backend")
    await bridge.flush()
    assert bridge.status()["queued"] == 1
    await bridge.flush()
    assert bridge.status()["queued"] == 0
    assert len(oracle.store.active_facts("Local")) == 1
    bridge.close()


@pytest.mark.asyncio
async def test_mcp_exposes_all_tools_and_registers(ecosystem, tmp_path):
    from oracle.mcp_server import create_mcp

    oracle, _ = ecosystem
    bridge = Bridge(tmp_path / "mcp.db", LocalTransport(oracle, "alice"), "alice", "backend")
    mcp = create_mcp(bridge)
    tools = await mcp.list_tools()
    assert len(tools) == 14
    assert {"oracle_gate", "oracle_ack_pause", "oracle_reply", "oracle_checkpoint"} <= {t.name for t in tools}
    registration = Registration(
        project_id="backend",
        repository="backend",
        human_id="alice",
        machine_id="mcp",
        agent_type="mcp-test",
        agent_session_id="mcp",
    )
    result = await mcp.call_tool("oracle_register", {"registration": registration.model_dump(mode="json")})
    assert "registered" in str(result)
    bridge.close()


class FakeProvider:
    last_metrics = {"model": "test"}
    bad = False

    async def structured_completion(self, task, context, schema):
        from oracle.protocol import ClarificationRequest, ConflictAssessment

        ids = ["invented"] if self.bad else [f["fact_id"] for f in context["facts"]]
        if schema is ClarificationRequest:
            return schema(
                subject=context["subject"],
                question="Which canonical identity type should every component use?",
                options=["UUID", "integer"],
                recommendation="UUID",
                evidence_fact_ids=ids,
            )
        assert schema is ConflictAssessment
        return schema(
            classification="CONSISTENT",
            confidence=0.9,
            severity="low",
            subject=context["subject"],
            summary="Compatible",
            evidence_fact_ids=ids,
            reason="Same type",
            recommended_action="none",
        )


@pytest.mark.asyncio
async def test_structured_worker_rejects_fabricated_evidence(ecosystem):
    oracle, sessions = ecosystem
    report(oracle, sessions, "alice", "User.id", "UUID")
    assert oracle.store.one("SELECT count(*) AS n FROM reasoning_jobs")["n"] == 0  # nothing to compare yet
    qid = report(oracle, sessions, "bob", "User.id", "integer")["question_id"]
    provider = FakeProvider()
    provider.bad = True
    worker = ReasoningWorker(oracle, provider)
    # Two jobs were queued: clarification-generator for the question and conflict-detector for the subject.
    statuses = [(await worker.once())["status"] for _ in range(4)]
    assert statuses == ["PENDING", "FAILED", "PENDING", "FAILED"]
    assert (await worker.once())["status"] == "IDLE"
    assert oracle.store.record("questions", qid)["suggestion"] is None
    assert not oracle.store.records("decisions")


@pytest.mark.asyncio
async def test_clarification_suggestion_is_owner_facing_only(ecosystem):
    oracle, sessions = ecosystem
    report(oracle, sessions, "alice", "User.id", "UUID")
    qid = report(oracle, sessions, "bob", "User.id", "integer")["question_id"]
    results = await ReasoningWorker(oracle, FakeProvider()).drain()
    assert {r["task"] for r in results} == {"clarification-generator", "conflict-detector"}
    question = oracle.store.record("questions", qid)
    assert question["suggestion"]["recommendation"] == "UUID"
    assert question["assessment"]["classification"] == "CONSISTENT"
    view = oracle.question_view("owner", qid)
    assert {p["value"] for p in view["positions"]} == {"UUID", "integer"}
    assert view["suggestion"]["options"] == ["UUID", "integer"]
    # Coding sessions only ever receive the pause reference, never the model text.
    for human, sid in sessions.items():
        assert "canonical identity" not in str(oracle.poll(human, sid))
    with pytest.raises(Exception):
        oracle.question_view("bob", qid)


@pytest.mark.asyncio
async def test_model_adapter_handles_schema_failure():
    from oracle.model import OpenAICompatibleProvider

    with pytest.raises(ValueError, match="Local"):
        OpenAICompatibleProvider("https://example.com/v1", "test")


def test_git_observer_excludes_secrets(tmp_path):
    import subprocess
    from oracle.observer import inspect_git

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".env").write_text("SECRET=do-not-share")
    (tmp_path / "app.py").write_text("print(1)")
    state, files = inspect_git(tmp_path)
    assert state.dirty
    assert "app.py" in files
    assert ".env" not in files


@pytest.mark.asyncio
async def test_model_extraction_cannot_promote_authority_or_disclosure(ecosystem):
    from oracle.protocol import ContextPacket, KnowledgeUpdate
    from conftest import send

    oracle, sessions = ecosystem
    packet = ContextPacket(
        session_id=sessions["alice"],
        human_intent="Make login work",
        agent_interpretation="Use UUIDs and JWT bearer",
    )
    send(oracle, "alice", "backend", sessions["alice"], EventType.CHECKPOINT, packet.model_dump(mode="json"))

    class Extractor:
        last_metrics = {"model": "fixture"}

        async def structured_completion(self, task, context, schema):
            assert schema is KnowledgeUpdate
            return schema(
                facts=[
                    {
                        "subject": "User.id",
                        "predicate": "type",
                        "value": "UUID",
                        "source": "human_decision",
                        "confidence": 1,
                        "visibility": "PUBLIC_CONTRACT",
                    }
                ]
            )

    assert (await ReasoningWorker(oracle, Extractor()).once())["status"] == "COMPLETE"
    facts = oracle.store.records("facts")
    assert len(facts) == 1
    assert facts[0]["source"] == "oracle_inference"
    assert facts[0]["visibility"] == "OWNER_ONLY"
    assert facts[0]["lifecycle"] == "PROPOSED"
    assert facts[0]["confidence"] == 0.7
    assert oracle.context("bob", sessions["bob"]) == []


class Mediator:
    last_metrics = {"model": "fixture"}

    def __init__(self, requires_human):
        self.requires_human = requires_human

    async def structured_completion(self, task, context, schema):
        from oracle.protocol import ConflictAssessment, MediationProposal

        if schema is ConflictAssessment:
            return await FakeProvider().structured_completion(task, context, schema)
        assert schema is MediationProposal and task == "mediation-agent"
        assert context["previous_responses"] == [] and "positions" in context
        return schema(
            subject=context["subject"],
            predicate=context["predicate"],
            value="UTF-8",
            reason="Equivalent spellings; normalize to the IANA name",
            evidence_fact_ids=[f["fact_id"] for f in context["facts"]],
            requires_human=self.requires_human,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("requires_human", [False, True])
async def test_mediation_suggestion_auto_proposes_only_for_low_risk(ecosystem, requires_human):
    from oracle.protocol import EventType
    from conftest import send

    oracle, sessions = ecosystem
    report(oracle, sessions, "alice", "InternalEncoding", "UTF-8", EventType.DECISION_UPDATE, visibility="PUBLIC_CONTRACT")
    cid = report(oracle, sessions, "bob", "InternalEncoding", "utf8", EventType.DECISION_UPDATE, visibility="PUBLIC_CONTRACT")["conflict_id"]
    for human, project in [("alice", "backend"), ("bob", "web")]:
        send(oracle, human, project, sessions[human], EventType.PAUSE_ACK, {"conflict_id": cid})
    jobs = [json.loads(r["data"])["task"] for r in oracle.store.rows("SELECT data FROM reasoning_jobs WHERE state='PENDING'")]
    assert "mediation-agent" in jobs
    results = await ReasoningWorker(oracle, Mediator(requires_human)).drain()
    assert all(r["status"] == "COMPLETE" for r in results)
    conflict = oracle.store.record("conflicts", cid)
    assert conflict["suggested_proposal"]["value"] == "UTF-8"
    if requires_human:
        assert conflict["state"] == "PAUSED" and conflict["round"] == 0
        # The owner adopts the validated suggestion explicitly.
        assert oracle.propose("owner", cid, use_suggestion=True)["round"] == 1
        assert oracle.store.record("conflicts", cid)["proposal"]["actor"] == "owner"
    else:
        assert conflict["state"] == "NEGOTIATION" and conflict["proposal"]["actor"] == "oracle-model"
        messages = oracle.poll("alice", sessions["alice"])["messages"]
        proposal = [m for m in messages if m["type"] == "MEDIATION_PROPOSAL"][0]["payload"]
        assert proposal["value"] == "UTF-8"


def test_grammar_schema_and_length_tolerant_parsing():
    from oracle.model import ModelFailure, inline_schema, parse_output
    from oracle.protocol import ConflictAssessment

    schema = inline_schema(ConflictAssessment)
    assert "$defs" not in json.dumps(schema) and "maxLength" not in json.dumps(schema)
    assert schema["properties"]["classification"]["enum"][0] == "CONSISTENT"
    payload = {
        "classification": "CONSISTENT",
        "confidence": 0.9,
        "severity": "low",
        "subject": "x",
        "summary": "s" * 5000,
        "evidence_fact_ids": ["a"],
        "reason": "r",
        "recommended_action": "none",
    }
    parsed = parse_output(ConflictAssessment, json.dumps(payload))
    assert len(parsed.summary) == 2000
    payload["confidence"] = 7
    with pytest.raises(ModelFailure, match="confidence"):
        parse_output(ConflictAssessment, json.dumps(payload))
    with pytest.raises(ModelFailure, match="invalid JSON"):
        parse_output(ConflictAssessment, "not json")
