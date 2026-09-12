"""Shared-password auth and identity resolution.

The password says you may write to the log. The X-Oracle-User header says
whose session this is. Oracle routes context to people, so identity has to
survive even when authentication stops being per-person (PRINCIPLES P5).
"""

from __future__ import annotations

import uuid

import pytest
from conftest import fixture_lines  # noqa: F401  (keeps sys.path setup)

from oracle_gateway import db
from oracle_gateway.auth import display_name_for
from oracle_gateway.config import SETTINGS


def test_display_name_is_derived_from_an_email_local_part():
    assert display_name_for("alice@acme.com") == "Alice"


def test_display_name_handles_a_bare_handle():
    assert display_name_for("bob") == "Bob"


def test_display_name_splits_dotted_names():
    assert display_name_for("ada.lovelace@acme.com") == "Ada Lovelace"


@pytest.fixture
async def api():
    """The real app, so the auth dependency and DB wiring are both exercised."""
    from fastapi.testclient import TestClient

    from oracle_gateway.main import app

    try:
        await db.connect()
        if not await db.ping():
            pytest.skip("database is not answering")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no database at DATABASE_URL: {exc}")
    await db.close()

    created: list[str] = []
    with TestClient(app) as client:
        yield client, created

    await db.connect()
    async with db.pool().acquire() as conn:
        for identity in created:
            await conn.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email=$1)", identity)
            await conn.execute("DELETE FROM users WHERE email = $1", identity)
    await db.close()


def test_the_right_password_is_accepted(api):
    client, _ = api
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {SETTINGS.password}"})
    assert response.status_code == 200


def test_the_wrong_password_is_rejected(api):
    client, _ = api
    response = client.get("/v1/me", headers={"Authorization": "Bearer not-the-password"})
    assert response.status_code == 401


def test_no_password_at_all_is_rejected(api):
    client, _ = api
    assert client.get("/v1/me").status_code == 401


def test_the_user_header_identifies_the_person_and_creates_them(api):
    client, created = api
    identity = f"newperson-{uuid.uuid4().hex[:8]}@acme.test"
    created.append(identity)

    body = client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {SETTINGS.password}", "X-Oracle-User": identity},
    ).json()

    assert body["email"] == identity
    assert body["display_name"].lower().startswith("newperson")


def test_the_same_person_resolves_to_one_row_every_time(api):
    client, created = api
    identity = f"repeat-{uuid.uuid4().hex[:8]}@acme.test"
    created.append(identity)
    headers = {"Authorization": f"Bearer {SETTINGS.password}", "X-Oracle-User": identity}

    first = client.get("/v1/me", headers=headers).json()
    second = client.get("/v1/me", headers=headers).json()

    assert first["id"] == second["id"]


def test_identity_is_case_insensitive(api):
    client, created = api
    identity = f"case-{uuid.uuid4().hex[:8]}@acme.test"
    created.append(identity)
    base = {"Authorization": f"Bearer {SETTINGS.password}"}

    lower = client.get("/v1/me", headers={**base, "X-Oracle-User": identity}).json()
    upper = client.get("/v1/me", headers={**base, "X-Oracle-User": identity.upper()}).json()

    assert lower["id"] == upper["id"]


def test_omitting_the_header_falls_back_to_the_shared_user(api):
    client, _ = api
    body = client.get("/v1/me", headers={"Authorization": f"Bearer {SETTINGS.password}"}).json()
    assert body["email"] == SETTINGS.default_user


def test_two_people_sharing_the_password_still_land_as_two_users(api):
    client, created = api
    a = f"a-{uuid.uuid4().hex[:8]}@acme.test"
    b = f"b-{uuid.uuid4().hex[:8]}@acme.test"
    created.extend([a, b])
    base = {"Authorization": f"Bearer {SETTINGS.password}"}

    one = client.get("/v1/me", headers={**base, "X-Oracle-User": a}).json()
    two = client.get("/v1/me", headers={**base, "X-Oracle-User": b}).json()

    assert one["id"] != two["id"]


def test_a_session_is_attributed_to_the_header_user_not_the_password(api):
    client, created = api
    identity = f"shipper-{uuid.uuid4().hex[:8]}@acme.test"
    created.append(identity)
    headers = {"Authorization": f"Bearer {SETTINGS.password}", "X-Oracle-User": identity}

    client.post(
        "/v1/ingest",
        headers=headers,
        json={
            "session": {"external_id": f"attr-{uuid.uuid4().hex[:6]}", "agent_kind": "custom"},
            "events": [
                {
                    "external_id": "e1",
                    "type": "user_message",
                    "role": "user",
                    "content": [{"type": "text", "text": "whose session is this"}],
                    "occurred_at": "2026-09-12T18:00:00Z",
                }
            ],
        },
    )
    sessions = client.get("/v1/sessions?limit=50", headers=headers).json()
    assert any(s["user_email"] == identity for s in sessions)
