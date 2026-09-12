import pytest

from oracle.protocol import Envelope, EventType, Registration
from oracle.service import Oracle
from oracle.store import Store


def send(oracle, human, project, session, kind, payload, **kwargs):
    event = Envelope(
        sender=human, project_id=project, session_id=session, type=kind, payload=payload, **kwargs
    )
    return oracle.ingest(human, event)


def register(oracle, human, project, interests, suffix="1"):
    registration = Registration(
        project_id=project,
        repository=project,
        human_id=human,
        machine_id=human + "-machine",
        agent_type="generic",
        agent_session_id=human + "-" + suffix,
        interests=interests,
    )
    return send(
        oracle, human, project, None, EventType.SESSION_REGISTER, registration.model_dump(mode="json")
    )["oracle_session_id"]


@pytest.fixture
def ecosystem(tmp_path):
    store = Store(tmp_path / "oracle.db")
    oracle = Oracle(store)
    for project, human in [("backend", "alice"), ("web", "bob"), ("analytics", "carol")]:
        oracle.create_project(
            project, "owner", rules={"oracle_arbitration": True, "low_risk_subjects": ["InternalEncoding"]}
        )
        oracle.grant("owner", project, human)
    for subject in ["User.id", "AuthenticationResponse", "InternalEncoding"]:
        oracle.share("owner", "backend", "web", subject)
        oracle.share("owner", "backend", "analytics", subject)
    sessions = {
        "alice": register(
            oracle, "alice", "backend", ["User.id", "AuthenticationResponse", "InternalEncoding"]
        ),
        "bob": register(oracle, "bob", "web", ["User.id", "AuthenticationResponse", "InternalEncoding"]),
        "carol": register(oracle, "carol", "analytics", ["User.id"]),
    }
    yield oracle, sessions
    store.close()


def report(oracle, sessions, human, subject, value, kind=EventType.ASSUMPTION_UPDATE, **extra):
    project = {"alice": "backend", "bob": "web", "carol": "analytics"}[human]
    return send(
        oracle,
        human,
        project,
        sessions[human],
        kind,
        {
            "subject": subject,
            "predicate": "type",
            "value": value,
            "visibility": "DEPENDENCY_CONSUMERS",
            "consumer_projects": ["web", "analytics"],
            **extra,
        },
    )


def ack_decision(oracle, sessions, decision):
    for human, sid in sessions.items():
        if sid in decision["affected_sessions"]:
            project = {"alice": "backend", "bob": "web", "carol": "analytics"}[human]
            send(
                oracle, human, project, sid, EventType.DECISION_ACK, {"decision_id": decision["decision_id"]}
            )
