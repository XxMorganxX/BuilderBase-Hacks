"""The write path against a real Postgres.

Skipped unless DATABASE_URL points at a reachable database, so the adapter
tests stay runnable anywhere:

    DATABASE_URL=postgresql://oracle:oracle@localhost:55432/oracle pytest
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from conftest import fixture_lines

from oracle_gateway import db, ingest
from oracle_gateway.adapters import get_adapter
from oracle_gateway.auth import User
from oracle_gateway.models import EventIn, SessionIn


@pytest.fixture
async def pool():
    # Function scoped on purpose: pytest-asyncio gives each test its own event
    # loop, and an asyncpg pool cannot be shared across loops.
    try:
        await db.connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no database at DATABASE_URL: {exc}")
    if not await db.ping():  # pragma: no cover
        pytest.skip("database is not answering")
    yield
    await db.close()


@pytest.fixture
async def user(pool):
    email = f"test-{uuid.uuid4().hex[:8]}@oracle.test"
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO users (email, display_name) VALUES ($1, $2) RETURNING id",
            email,
            "Test User",
        )
    created = User(id=row["id"], email=email, display_name="Test User")
    yield created
    async with db.pool().acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE user_id = $1", created.id)
        await conn.execute("DELETE FROM users WHERE id = $1", created.id)


def a_session(**overrides) -> SessionIn:
    base = {
        "external_id": f"sess-{uuid.uuid4().hex[:10]}",
        "agent_kind": "custom",
        "workspace": "/tmp/demo",
    }
    return SessionIn(**{**base, **overrides})


def an_event(external_id: str, minutes: int = 0, **overrides) -> EventIn:
    base = {
        "external_id": external_id,
        "type": "user_message",
        "role": "user",
        "content": [{"type": "text", "text": f"message {external_id}"}],
        "occurred_at": datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
    }
    return EventIn(**{**base, **overrides})


async def test_re_sending_the_same_batch_inserts_nothing_the_second_time(user):
    session = a_session()
    events = [an_event("e1"), an_event("e2", 1)]

    first = await ingest.ingest_batch(user, session, events)
    second = await ingest.ingest_batch(user, session, events)

    assert (first.received, first.inserted, first.duplicates) == (2, 2, 0)
    assert (second.received, second.inserted, second.duplicates) == (2, 0, 2)
    assert second.session_id == first.session_id


async def test_event_count_reflects_only_rows_actually_written(user):
    session = a_session()
    first = await ingest.ingest_batch(user, session, [an_event("e1")])
    # e1 repeats, e2 is new: the counter must move by one, not by two.
    await ingest.ingest_batch(user, session, [an_event("e1"), an_event("e2", 1)])

    row = await db.get_session(first.session_id)
    assert row["event_count"] == 2


async def test_duplicate_ids_inside_one_batch_collapse(user):
    session = a_session()
    result = await ingest.ingest_batch(user, session, [an_event("same"), an_event("same", 1)])
    assert (result.received, result.inserted, result.duplicates) == (2, 1, 1)


async def test_seq_is_assigned_per_session_and_keeps_climbing_across_batches(user):
    session = a_session()
    first = await ingest.ingest_batch(user, session, [an_event("a"), an_event("b", 1)])
    await ingest.ingest_batch(user, session, [an_event("c", 2)])

    rows = await db.list_session_events(first.session_id, after_seq=-1, limit=10)
    assert [(r["external_event_id"], r["seq"]) for r in rows] == [("a", 0), ("b", 1), ("c", 2)]


async def test_explicit_seq_from_an_adapter_is_preserved(user):
    session = a_session()
    result = await ingest.ingest_batch(user, session, [an_event("a", seq=41), an_event("b", 1, seq=42)])
    rows = await db.list_session_events(result.session_id, after_seq=-1, limit=10)
    assert [r["seq"] for r in rows] == [41, 42]


async def test_a_later_thin_batch_does_not_erase_session_header_fields(user):
    rich = a_session(branch="feature/refunds", agent_version="2.1.258", title="Add refunds")
    thin = a_session(external_id=rich.external_id, agent_kind=rich.agent_kind)

    result = await ingest.ingest_batch(user, rich, [an_event("a")])
    await ingest.ingest_batch(user, thin, [an_event("b", 1)])

    row = await db.get_session(result.session_id)
    assert row["branch"] == "feature/refunds"
    assert row["agent_version"] == "2.1.258"
    assert row["title"] == "Add refunds"


async def test_last_event_at_tracks_the_newest_event(user):
    session = a_session()
    result = await ingest.ingest_batch(user, session, [an_event("a"), an_event("b", 90)])
    row = await db.get_session(result.session_id)
    assert row["last_event_at"] == datetime(2026, 9, 12, 19, 30, tzinfo=timezone.utc)


async def test_two_users_may_ship_the_same_vendor_session_id(user, pool):
    async with db.pool().acquire() as conn:
        other_row = await conn.fetchrow(
            "INSERT INTO users (email, display_name) VALUES ($1, $2) RETURNING id",
            f"other-{uuid.uuid4().hex[:8]}@oracle.test",
            "Other",
        )
    other = User(id=other_row["id"], email="other@oracle.test", display_name="Other")
    session = a_session()
    try:
        mine = await ingest.ingest_batch(user, session, [an_event("a")])
        theirs = await ingest.ingest_batch(other, session, [an_event("a")])
        assert mine.session_id != theirs.session_id
    finally:
        async with db.pool().acquire() as conn:
            await conn.execute("DELETE FROM sessions WHERE user_id = $1", other.id)
            await conn.execute("DELETE FROM users WHERE id = $1", other.id)


async def test_a_real_claude_code_file_lands_and_re_shipping_is_free(user):
    batch = get_adapter("claude_code").parse(fixture_lines("claude_code_sample.jsonl"))
    unique_session = batch.session.model_copy(update={"external_id": f"cc-{uuid.uuid4().hex[:8]}"})

    first = await ingest.ingest_batch(user, unique_session, batch.events, skipped=batch.skipped)
    second = await ingest.ingest_batch(user, unique_session, batch.events, skipped=batch.skipped)

    assert first.inserted == 8
    assert first.skipped == 7
    assert second.inserted == 0

    rows = await db.list_session_events(first.session_id, after_seq=-1, limit=50)
    assert [r["event_type"] for r in rows] == [
        "user_message",
        "user_message",
        "user_message",
        "thinking",
        "assistant_message",
        "tool_call",
        "tool_result",
        "summary",
    ]
    assert all(r["content"] for r in rows)


async def test_events_are_visible_on_the_global_cursor_feed(user):
    session = a_session()
    result = await ingest.ingest_batch(user, session, [an_event("a")])
    rows = await db.list_events(after_id=0, limit=1000, user_id=user.id)
    assert any(r["session_id"] == result.session_id and r["user_id"] == user.id for r in rows)


async def test_a_title_discovered_later_replaces_an_inferred_one(user):
    """Claude Code emits its ai-title several turns in, so titles arrive late."""
    first = a_session(title="Fix the thing")
    later = a_session(external_id=first.external_id, title="Refund endpoint for payments")

    result = await ingest.ingest_batch(user, first, [an_event("a")])
    await ingest.ingest_batch(user, later, [an_event("b", 1)])

    row = await db.get_session(result.session_id)
    assert row["title"] == "Refund endpoint for payments"
