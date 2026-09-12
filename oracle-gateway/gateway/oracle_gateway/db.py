"""asyncpg pool and every SQL statement in the gateway.

All SQL lives here. Callers pass and receive plain Python objects, so the
storage engine can be replaced by editing this file and db/schema.sql only
(PRINCIPLES P7).
"""

from __future__ import annotations

import json
from typing import Any, Iterable
from uuid import UUID

import asyncpg

from .config import SETTINGS

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Pass Python dicts/lists straight into jsonb columns and back."""
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename,
            encoder=lambda v: json.dumps(v, ensure_ascii=False, default=str),
            decoder=json.loads,
            schema="pg_catalog",
        )


async def connect() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=SETTINGS.database_url,
            min_size=SETTINGS.db_pool_min,
            max_size=SETTINGS.db_pool_max,
            command_timeout=SETTINGS.db_command_timeout,
            init=_init_connection,
        )
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not initialised")
    return _pool


async def ping() -> bool:
    try:
        async with pool().acquire() as conn:
            return await conn.fetchval("SELECT 1") == 1
    except Exception:
        return False


# -- identity ---------------------------------------------------------------

# Users appear the first time someone ships as them. There is no provisioning
# step: the identity in the request header is the whole of the registration.
UPSERT_USER = """
INSERT INTO users (email, display_name) VALUES ($1, $2)
ON CONFLICT (email) DO UPDATE SET display_name = users.display_name
RETURNING id, email, display_name
"""


async def upsert_user(identity: str, display_name: str) -> asyncpg.Record:
    async with pool().acquire() as conn:
        return await conn.fetchrow(UPSERT_USER, identity, display_name)


# -- ingest (write path) ----------------------------------------------------

# Nullable header fields are only overwritten when the incoming value is non-null,
# so a later thin batch never erases what an earlier rich batch established.
UPSERT_SESSION = """
INSERT INTO sessions (
    user_id, agent_kind, agent_version, external_session_id,
    workspace, repo, branch, title, started_at, ended_at, metadata
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (user_id, agent_kind, external_session_id) DO UPDATE SET
    agent_version = COALESCE(EXCLUDED.agent_version, sessions.agent_version),
    workspace     = COALESCE(EXCLUDED.workspace,     sessions.workspace),
    repo          = COALESCE(EXCLUDED.repo,          sessions.repo),
    branch        = COALESCE(EXCLUDED.branch,        sessions.branch),
    -- A non-null incoming title wins: adapters learn the real title partway
    -- through a session (Claude Code emits ai-title several turns in).
    title         = COALESCE(EXCLUDED.title,         sessions.title),
    started_at    = LEAST(sessions.started_at, COALESCE(EXCLUDED.started_at, sessions.started_at)),
    ended_at      = COALESCE(EXCLUDED.ended_at,      sessions.ended_at),
    metadata      = sessions.metadata || EXCLUDED.metadata,
    updated_at    = now()
RETURNING id
"""

MAX_SEQ = "SELECT COALESCE(MAX(seq), -1) FROM events WHERE session_id = $1"

# One statement for the whole batch. Duplicates are success (P4): the unique
# index on (session_id, external_event_id) absorbs re-sent lines.
INSERT_EVENTS = """
INSERT INTO events (
    session_id, seq, external_event_id, parent_external_id, event_type, role,
    content, content_text, model, usage, occurred_at, metadata, raw
)
SELECT
    $1::uuid,
    (e->>'seq')::int,
    e->>'external_id',
    e->>'parent_external_id',
    e->>'type',
    e->>'role',
    COALESCE(NULLIF(e->'content', 'null'::jsonb), '[]'::jsonb),
    e->>'text',
    e->>'model',
    NULLIF(e->'usage', 'null'::jsonb),
    (e->>'occurred_at')::timestamptz,
    COALESCE(NULLIF(e->'metadata', 'null'::jsonb), '{}'::jsonb),
    NULLIF(e->'raw', 'null'::jsonb)
FROM jsonb_array_elements($2::jsonb) AS e
ON CONFLICT (session_id, external_event_id) DO NOTHING
RETURNING id
"""

TOUCH_SESSION = """
UPDATE sessions SET
    last_event_at = GREATEST(last_event_at, $2::timestamptz),
    event_count   = event_count + $3,
    updated_at    = now()
WHERE id = $1
"""


async def ingest_batch(
    user_id: UUID,
    session_row: dict[str, Any],
    event_rows: list[dict[str, Any]],
    latest_occurred_at: Any | None = None,
) -> tuple[UUID, int]:
    """Upsert the session and append events in one transaction.

    Returns (session_id, inserted_count). The caller has already de-duplicated
    external ids within the batch; any event whose seq is None is numbered here.
    """
    async with pool().acquire() as conn:
        async with conn.transaction():
            session_id: UUID = await conn.fetchval(
                UPSERT_SESSION,
                user_id,
                session_row["agent_kind"],
                session_row.get("agent_version"),
                session_row["external_id"],
                session_row.get("workspace"),
                session_row.get("repo"),
                session_row.get("branch"),
                session_row.get("title"),
                session_row.get("started_at"),
                session_row.get("ended_at"),
                session_row.get("metadata") or {},
            )
            if not event_rows:
                return session_id, 0

            # Assign seq inside the transaction so concurrent batches for the
            # same session cannot both claim the same starting number.
            if any(e.get("seq") is None for e in event_rows):
                nxt = (await conn.fetchval(MAX_SEQ, session_id)) + 1
                for row in event_rows:
                    if row.get("seq") is None:
                        row["seq"] = nxt
                        nxt += 1

            inserted = await conn.fetch(INSERT_EVENTS, session_id, event_rows)
            count = len(inserted)
            if count and latest_occurred_at is not None:
                await conn.execute(TOUCH_SESSION, session_id, latest_occurred_at, count)
            return session_id, count


async def session_id_for(user_id: UUID, agent_kind: str, external_id: str) -> UUID | None:
    async with pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM sessions WHERE user_id=$1 AND agent_kind=$2 AND external_session_id=$3",
            user_id,
            agent_kind,
            external_id,
        )


# -- read path --------------------------------------------------------------

SESSION_COLUMNS = """
    s.id, s.user_id, u.email AS user_email, s.agent_kind, s.agent_version,
    s.external_session_id, s.workspace, s.repo, s.branch, s.title,
    s.started_at, s.last_event_at, s.ended_at, s.event_count, s.metadata
"""

EVENT_COLUMNS = """
    e.id, e.session_id, e.seq, e.external_event_id, e.parent_external_id,
    e.event_type, e.role, e.content, e.content_text, e.model, e.usage,
    e.occurred_at, e.ingested_at, e.metadata
"""


async def list_sessions(
    user_id: UUID | None,
    agent_kind: str | None,
    since: Any | None,
    limit: int,
) -> list[asyncpg.Record]:
    sql = f"""
    SELECT {SESSION_COLUMNS}
    FROM sessions s JOIN users u ON u.id = s.user_id
    WHERE ($1::uuid IS NULL OR s.user_id = $1)
      AND ($2::text IS NULL OR s.agent_kind = $2)
      AND ($3::timestamptz IS NULL OR s.last_event_at >= $3)
    ORDER BY s.last_event_at DESC NULLS LAST, s.created_at DESC
    LIMIT $4
    """
    async with pool().acquire() as conn:
        return await conn.fetch(sql, user_id, agent_kind, since, limit)


async def get_session(session_id: UUID) -> asyncpg.Record | None:
    sql = f"""
    SELECT {SESSION_COLUMNS}
    FROM sessions s JOIN users u ON u.id = s.user_id
    WHERE s.id = $1
    """
    async with pool().acquire() as conn:
        return await conn.fetchrow(sql, session_id)


async def list_session_events(session_id: UUID, after_seq: int, limit: int) -> list[asyncpg.Record]:
    sql = f"""
    SELECT {EVENT_COLUMNS}
    FROM events e
    WHERE e.session_id = $1 AND e.seq > $2
    ORDER BY e.seq, e.id
    LIMIT $3
    """
    async with pool().acquire() as conn:
        return await conn.fetch(sql, session_id, after_seq, limit)


async def list_events(
    after_id: int,
    limit: int,
    types: Iterable[str] | None = None,
    user_id: UUID | None = None,
) -> list[asyncpg.Record]:
    """The Oracle cursor feed: global insertion order, with owning user (D6)."""
    type_list = list(types) if types else None
    sql = f"""
    SELECT {EVENT_COLUMNS}, s.user_id
    FROM events e JOIN sessions s ON s.id = e.session_id
    WHERE e.id > $1
      AND ($3::text[] IS NULL OR e.event_type = ANY($3))
      AND ($4::uuid IS NULL OR s.user_id = $4)
    ORDER BY e.id
    LIMIT $2
    """
    async with pool().acquire() as conn:
        return await conn.fetch(sql, after_id, limit, type_list, user_id)


async def pending_messages(user_id: UUID, session_id: UUID | None) -> list[asyncpg.Record]:
    """Phase-3 inbox. The table exists now; the Oracle agent fills it later."""
    sql = """
    SELECT id, target_session_id, source_session_id, kind, body, priority, created_at, metadata
    FROM oracle_messages
    WHERE target_user_id = $1
      AND delivered_at IS NULL
      AND ($2::uuid IS NULL OR target_session_id IS NULL OR target_session_id = $2)
    ORDER BY priority DESC, created_at
    """
    async with pool().acquire() as conn:
        return await conn.fetch(sql, user_id, session_id)
