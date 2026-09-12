from datetime import datetime, timedelta
import json

import pytest
from pydantic import ValidationError

from oracle.policy import OracleError
from oracle.protocol import ContextPacket, Envelope, EventType
from oracle.service import Oracle
from oracle.store import Store
from conftest import ack_decision, register, report, send


def test_three_projects_clarification_and_selective_pause(ecosystem):
    oracle, sessions = ecosystem
    unrelated = register(oracle, "carol", "analytics", ["Theme"], "theme")
    report(oracle, sessions, "alice", "User.id", "UUID")
    result = report(oracle, sessions, "bob", "User.id", "integer")
    report(oracle, sessions, "carol", "User.id", "email")
    assert result["classification"] == "UNDERSPECIFIED"
    question = oracle.store.record("questions", result["question_id"])
    assert set(question["affected_sessions"]) == set(sessions.values())
    assert oracle.store.record("sessions", unrelated)["work_state"] == "RUNNING"
    decision = oracle.decide(
        "owner",
        "backend",
        "User.id",
        "type",
        "UUID",
        "Canonical project identity",
        question_id=question["question_id"],
    )
    assert oracle.store.record("questions", question["question_id"])["status"] == "ANSWERED"
    ack_decision(oracle, sessions, decision)
    for human, sid in sessions.items():
        assert oracle.store.record("sessions", sid)["work_state"] == "RUNNING"
        context = oracle.context(human, sid)
        assert any(f["value"] == "UUID" and f["source"] == "human_decision" for f in context)
        assert not any(f["value"] in ["integer", "email"] for f in context)
    assert not oracle.poll("carol", unrelated)["messages"]


def test_authority_is_not_confidence(ecosystem):
    oracle, sessions = ecosystem
    authoritative = oracle.decide("owner", "backend", "User.id", "type", "UUID", "Approved requirement")
    ack_decision(oracle, sessions, authoritative)
    result = report(oracle, sessions, "bob", "User.id", "integer", confidence=1.0)
    assert result["classification"] == "CONFLICTING"
    fact = oracle.store.record("facts", authoritative["fact_id"])
    assert fact["lifecycle"] == "ACTIVE"
    with pytest.raises(OracleError, match="authoritative"):
        report(oracle, sessions, "bob", "User.id", "integer", source="human_decision")
    with pytest.raises(OracleError):
        oracle.decide("bob", "backend", "User.id", "type", "integer", "I prefer this")


def test_principal_session_and_project_binding(ecosystem):
    oracle, sessions = ecosystem
    event = Envelope(sender="alice", project_id="backend", session_id=sessions["alice"], type="HEARTBEAT")
    with pytest.raises(OracleError):
        oracle.ingest("bob", event)
    with pytest.raises(OracleError):
        oracle.poll("bob", sessions["alice"])
    with pytest.raises(OracleError):
        send(oracle, "alice", "web", sessions["alice"], EventType.HEARTBEAT, {})
    from oracle.rpc import dispatch

    with pytest.raises(OracleError):
        dispatch(oracle, "alice", {"op": "decide", "value": "spoof"})


def test_duplicate_delivery_exactly_one_state_effect(ecosystem):
    oracle, sessions = ecosystem
    event = Envelope(
        sender="alice",
        project_id="backend",
        session_id=sessions["alice"],
        type="ASSUMPTION_UPDATE",
        payload={"subject": "User.id", "predicate": "type", "value": "UUID"},
    )
    first = oracle.ingest("alice", event)
    assert oracle.ingest("alice", event) == first
    assert len(oracle.store.active_facts("User.id")) == 1
    event.payload["value"] = "integer"
    with pytest.raises(OracleError, match="already used"):
        oracle.ingest("alice", event)


def test_checkpoint_atomicity_and_staleness(ecosystem):
    oracle, sessions = ecosystem
    packet = ContextPacket(
        session_id=sessions["alice"],
        assumptions=[
            {"subject": "User.id", "predicate": "type", "value": "UUID"},
            {"subject": "Private", "predicate": "value", "value": "fake", "source": "project_spec"},
        ],
    )
    with pytest.raises(OracleError):
        send(
            oracle,
            "alice",
            "backend",
            sessions["alice"],
            EventType.CHECKPOINT,
            packet.model_dump(mode="json"),
        )
    assert not oracle.store.active_facts("User.id")
    packet.assumptions = packet.assumptions[:1]
    send(oracle, "alice", "backend", sessions["alice"], EventType.CHECKPOINT, packet.model_dump(mode="json"))
    packet.timestamp -= timedelta(seconds=60)
    packet.assumptions[0].value = "integer"
    result = send(
        oracle, "alice", "backend", sessions["alice"], EventType.CHECKPOINT, packet.model_dump(mode="json")
    )
    assert result["classification"] == "STALE"
    assert oracle.store.active_facts("User.id")[0]["value"] == "UUID"


def test_visibility_and_non_leaking_propagation(ecosystem):
    oracle, sessions = ecosystem
    for visibility in ["ORACLE_ONLY", "OWNER_ONLY", "PROJECT"]:
        report(oracle, sessions, "alice", "User.id", "secret-" + visibility, visibility=visibility)
        assert "secret-" not in json.dumps(oracle.context("bob", sessions["bob"]))
    assert "secret-" not in json.dumps(oracle.poll("bob", sessions["bob"]))
    oracle.create_project("unrelated", "owner")
    oracle.grant("owner", "unrelated", "outsider")
    other = register(oracle, "outsider", "unrelated", ["User.id"])
    report(oracle, sessions, "alice", "User.id", "UUID")
    assert not oracle.context("outsider", other)
    assert oracle.store.record("sessions", other)["work_state"] == "RUNNING"


def conflict_setup(oracle, sessions, subject="AuthenticationResponse"):
    report(oracle, sessions, "alice", subject, "token", EventType.DECISION_UPDATE)
    result = report(oracle, sessions, "bob", subject, "cookie", EventType.DECISION_UPDATE)
    conflict_id = result["conflict_id"]
    for human, project in [("alice", "backend"), ("bob", "web")]:
        send(oracle, human, project, sessions[human], EventType.PAUSE_ACK, {"conflict_id": conflict_id})
    return conflict_id


def test_mediation_bounded_rounds_and_human_escalation(ecosystem):
    oracle, sessions = ecosystem
    cid = conflict_setup(oracle, sessions)
    for round_number in range(1, 4):
        oracle.propose("owner", cid, "access_token + expires_at", "Shared contract")
        for human, project in [("alice", "backend"), ("bob", "web")]:
            payload = {"conflict_id": cid, "position": "REJECT", "reason": "Need revision"}
            send(oracle, human, project, sessions[human], EventType.MEDIATION_RESPONSE, payload)
            if human == "alice":
                with pytest.raises(OracleError, match="One response"):
                    send(oracle, human, project, sessions[human], EventType.MEDIATION_RESPONSE, payload)
    assert oracle.store.record("conflicts", cid)["state"] == "HUMAN_ESCALATION"
    with pytest.raises(OracleError, match="round limit"):
        oracle.propose("owner", cid, "again", "Unbounded retry")
    decision = oracle.decide(
        "owner",
        "backend",
        "AuthenticationResponse",
        "type",
        "access_token + expires_at",
        "Owner chose final auth contract",
        conflict_id=cid,
    )
    ack_decision(oracle, sessions, decision)
    assert oracle.store.record("conflicts", cid)["state"] == "CLOSED"


def test_low_risk_oracle_arbitration_and_security_escalation(ecosystem):
    oracle, sessions = ecosystem
    cid = conflict_setup(oracle, sessions, "InternalEncoding")
    # Explicitly public contract facts permit a shared derived decision across both projects.
    for f in oracle.store.active_facts("InternalEncoding"):
        f["visibility"] = "PUBLIC_CONTRACT"
        oracle.store.save("facts", f)
    oracle.propose("owner", cid, "utf8", "Owner-approved equivalent internal encoding")
    for human, project in [("alice", "backend"), ("bob", "web")]:
        result = send(
            oracle,
            human,
            project,
            sessions[human],
            EventType.MEDIATION_RESPONSE,
            {"conflict_id": cid, "position": "ACCEPT", "reason": "Compatible"},
        )
    assert result["decision_id"]
    decision = oracle.store.record("decisions", result["decision_id"])
    assert decision["authority"] == "oracle_arbitration"
    ack_decision(oracle, sessions, decision)


def test_multiple_blockers_prevent_early_resume(ecosystem):
    oracle, sessions = ecosystem
    report(oracle, sessions, "alice", "User.id", "UUID")
    q = report(oracle, sessions, "bob", "User.id", "integer")["question_id"]
    cid = conflict_setup(oracle, sessions)
    decision = oracle.decide("owner", "backend", "User.id", "type", "UUID", "Identity", question_id=q)
    ack_decision(oracle, sessions, decision)
    assert oracle.store.record("sessions", sessions["alice"])["work_state"] != "RUNNING"
    assert oracle.store.record("conflicts", cid)["state"] == "PAUSED"


def test_restart_restores_open_negotiation_and_replay(ecosystem):
    oracle, sessions = ecosystem
    cid = conflict_setup(oracle, sessions)
    oracle.propose("owner", cid, "access_token", "Proposal")
    restarted_store = Store(oracle.store.path)
    restarted = Oracle(restarted_store)
    try:
        assert restarted.store.record("conflicts", cid)["state"] == "NEGOTIATION"
        initial = restarted.poll("alice", sessions["alice"])
        assert initial["messages"] == oracle.poll("alice", sessions["alice"])["messages"]
        assert not restarted.poll("alice", sessions["alice"], after=initial["next_cursor"])["messages"]
    finally:
        restarted_store.close()


def test_gate_requires_exact_current_authorization(ecosystem):
    oracle, sessions = ecosystem
    payload = {
        "action": "modify_contract",
        "target": "AuthenticationResponse",
        "proposed_change": "rename token",
        "revision": "abc123",
    }
    result = send(oracle, "alice", "backend", sessions["alice"], EventType.GATE_REQUEST, payload)
    assert result["status"] == "CLARIFICATION_REQUIRED"
    decision = oracle.decide(
        "owner",
        "backend",
        "AuthenticationResponse",
        "change_authorization",
        result["proposed_authorization"],
        "Approve exact change",
        question_id=result["question_id"],
    )
    ack_decision(oracle, sessions, decision)
    allowed = send(oracle, "alice", "backend", sessions["alice"], EventType.GATE_REQUEST, payload)
    assert allowed["status"] == "ALLOW"
    payload["revision"] = "different"
    assert (
        send(oracle, "alice", "backend", sessions["alice"], EventType.GATE_REQUEST, payload)["status"]
        != "ALLOW"
    )


def test_heartbeat_never_creates_model_work(ecosystem):
    oracle, sessions = ecosystem
    for _ in range(5):
        send(oracle, "alice", "backend", sessions["alice"], EventType.HEARTBEAT, {})
    assert oracle.store.one("SELECT count(*) AS n FROM reasoning_jobs")["n"] == 0


def test_protocol_rejects_unknown_versions_and_naive_times():
    with pytest.raises(ValidationError):
        Envelope(protocol_version=2, sender="a", project_id="p", type="HEARTBEAT")
    with pytest.raises(ValidationError):
        Envelope(sender="a", project_id="p", type="HEARTBEAT", timestamp=datetime.now())


def test_team_visibility_does_not_cross_organizations(ecosystem):
    oracle, sessions = ecosystem
    report(oracle, sessions, "alice", "User.id", "confidential-team-format", visibility="TEAM")
    oracle.create_project("other-org", "owner", team="default", organization="other")
    oracle.grant("owner", "other-org", "outsider")
    outsider = register(oracle, "outsider", "other-org", ["User.id"])
    assert oracle.context("outsider", outsider) == []


def test_repeated_conflicting_report_stays_conflicting(ecosystem):
    oracle, sessions = ecosystem
    decision = oracle.decide("owner", "backend", "User.id", "type", "UUID", "Canonical")
    ack_decision(oracle, sessions, decision)
    report(oracle, sessions, "bob", "User.id", "integer")
    result = report(oracle, sessions, "bob", "User.id", "integer")
    assert result["classification"] == "CONFLICTING"
    assert result["deduplicated"] is True


def test_late_pause_ack_cannot_repause_resolved_session(ecosystem):
    oracle, sessions = ecosystem
    report(oracle, sessions, "alice", "User.id", "UUID")
    qid = report(oracle, sessions, "bob", "User.id", "integer")["question_id"]
    decision = oracle.decide("owner", "backend", "User.id", "type", "UUID", "Canonical", question_id=qid)
    ack_decision(oracle, sessions, decision)
    with pytest.raises(OracleError, match="already been resolved"):
        send(oracle, "alice", "backend", sessions["alice"], EventType.PAUSE_ACK, {"question_id": qid})
    assert oracle.store.record("sessions", sessions["alice"])["work_state"] == "RUNNING"


def test_conflict_is_typed_deterministically(ecosystem):
    oracle, sessions = ecosystem
    for human, project, role, fields in [("alice", "backend", "PRODUCES", ["token"]), ("bob", "web", "CONSUMES", ["access_token"])]:
        result = send(
            oracle,
            human,
            project,
            sessions[human],
            EventType.INTERFACE_UPDATE,
            {"name": "AuthenticationResponse", "role": role, "contract": {"fields": fields}, "visibility": "PUBLIC_CONTRACT"},
        )
    assert result["results"][0]["conflict_type"] == "INTERFACE_MISMATCH"
    cid = conflict_setup(oracle, sessions, "InternalEncoding")  # plain agent decisions, "type" predicate
    assert oracle.store.record("conflicts", cid)["type"] == "SCHEMA_MISMATCH"
    decision = oracle.decide("owner", "backend", "User.id", "type", "UUID", "Canonical")
    ack_decision(oracle, sessions, decision)
    result = report(oracle, sessions, "bob", "User.id", "integer")
    assert result["conflict_type"] == "SPEC_VIOLATION"


def test_propagation_is_participant_specific(ecosystem):
    oracle, sessions = ecosystem
    report(oracle, sessions, "alice", "User.id", "UUID")
    q = report(oracle, sessions, "bob", "User.id", "integer")["question_id"]
    report(oracle, sessions, "carol", "User.id", "email")
    oracle.decide("owner", "backend", "User.id", "type", "UUID", "Canonical identity", question_id=q)
    messages = {
        human: [m for m in oracle.poll(human, sid)["messages"] if m["type"] == "FINAL_DECISION"][0]["payload"]
        for human, sid in sessions.items()
    }
    assert messages["alice"]["changed"] is False and "matching your current position" in messages["alice"]["instruction"]
    assert messages["bob"]["changed"] is True and messages["bob"]["previous_value"] == "integer"
    assert messages["carol"]["previous_value"] == "email"
    # Nobody learns another participant's position through propagation.
    assert "email" not in json.dumps(messages["bob"]) and "integer" not in json.dumps(messages["carol"])


def test_same_file_overlap_is_advisory_and_project_scoped(ecosystem):
    oracle, sessions = ecosystem
    second = register(oracle, "alice", "backend", ["User.id"], "second")
    packet = ContextPacket(session_id=sessions["alice"], files={"modified": ["src/auth.py"]}, symbols={"modified": ["login"]})
    send(oracle, "alice", "backend", sessions["alice"], EventType.CHECKPOINT, packet.model_dump(mode="json"))
    packet = ContextPacket(session_id=second, files={"modified": ["src/auth.py"]})
    result = send(oracle, "alice", "backend", second, EventType.CHECKPOINT, packet.model_dump(mode="json"))
    assert result["overlaps"][0]["object"] == "file:src/auth.py"
    assert result["overlaps"][0]["sessions"][0]["session_id"] == sessions["alice"]
    assert oracle.store.record("sessions", second)["work_state"] == "RUNNING"
    packet = ContextPacket(session_id=sessions["bob"], files={"modified": ["src/auth.py"]})
    result = send(oracle, "bob", "web", sessions["bob"], EventType.CHECKPOINT, packet.model_dump(mode="json"))
    assert result["overlaps"] == []  # another project's paths are never disclosed


def test_curator_retires_closed_session_assumptions(ecosystem):
    oracle, sessions = ecosystem
    report(oracle, sessions, "alice", "User.id", "UUID")
    second = register(oracle, "alice", "backend", ["User.id"], "second")
    send(oracle, "alice", "backend", second, EventType.ASSUMPTION_UPDATE, {"subject": "User.id", "predicate": "type", "value": "UUID"})
    send(oracle, "alice", "backend", sessions["alice"], EventType.SESSION_CLOSE, {})
    report_before = oracle.curate("owner", "backend")
    assert len(report_before["deprecated"]) == 1
    assert len(oracle.store.active_facts("User.id")) == 1
    with pytest.raises(OracleError):
        oracle.curate("alice", "backend")
