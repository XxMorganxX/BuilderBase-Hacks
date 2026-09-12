-- Oracle: session ingestion schema, phase 1. Postgres 16.
-- Apply: psql "$DATABASE_URL" -f db/schema.sql   (idempotent)
-- Source of truth for the database (docs/PRINCIPLES.md P8). Schema notes live in docs/PLAN.md section 5.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- Identity (P5) --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text NOT NULL UNIQUE,
  display_name  text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key_hash    text NOT NULL UNIQUE,          -- sha256 hex of the bearer token
  label       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  revoked_at  timestamptz
);

-- Sessions: the one mutable header row (P1) ----------------------------------
CREATE TABLE IF NOT EXISTS sessions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES users(id),
  agent_kind           text NOT NULL,        -- 'claude_code' | 'codex' | 'cursor' | 'custom' ...
  agent_version        text,
  external_session_id  text NOT NULL,        -- the vendor's own session id
  workspace            text,                 -- cwd / project root on the user's machine
  repo                 text,                 -- git remote, if known
  branch               text,
  title                text,
  started_at           timestamptz,
  last_event_at        timestamptz,
  ended_at             timestamptz,
  event_count          integer NOT NULL DEFAULT 0,
  metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, agent_kind, external_session_id)          -- D12
);
CREATE INDEX IF NOT EXISTS sessions_user_recent_idx ON sessions (user_id, last_event_at DESC);
CREATE INDEX IF NOT EXISTS sessions_recent_idx      ON sessions (last_event_at DESC);
CREATE INDEX IF NOT EXISTS sessions_workspace_idx   ON sessions (workspace);

-- Events: append-only (P1, P3) -----------------------------------------------
CREATE TABLE IF NOT EXISTS events (
  id                  bigserial PRIMARY KEY, -- global read cursor (D6)
  session_id          uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq                 integer NOT NULL,      -- per-session order
  external_event_id   text NOT NULL,         -- vendor id or shipper-derived stable id
  parent_external_id  text,
  event_type          text NOT NULL,
  role                text,
  content             jsonb NOT NULL DEFAULT '[]'::jsonb,   -- canonical blocks (PLAN 4.3)
  content_text        text,                                 -- flattened, for search
  model               text,
  usage               jsonb,
  occurred_at         timestamptz NOT NULL,
  ingested_at         timestamptz NOT NULL DEFAULT now(),
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
  raw                 jsonb,                                 -- original native payload
  UNIQUE (session_id, external_event_id),                    -- D5 idempotency
  CONSTRAINT events_type_chk CHECK (event_type IN
    ('user_message','assistant_message','thinking','tool_call','tool_result','system','summary','other')),
  CONSTRAINT events_role_chk CHECK (role IS NULL OR role IN ('user','assistant','system','tool'))
);
CREATE INDEX IF NOT EXISTS events_session_seq_idx ON events (session_id, seq);
CREATE INDEX IF NOT EXISTS events_occurred_idx    ON events (occurred_at);
CREATE INDEX IF NOT EXISTS events_type_idx        ON events (event_type);
CREATE INDEX IF NOT EXISTS events_text_fts_idx    ON events
  USING gin (to_tsvector('english', coalesce(content_text, '')));

-- Phase 2/3: reserved now so the Oracle agent needs no migration (D11) --------
CREATE TABLE IF NOT EXISTS oracle_cursors (
  name           text PRIMARY KEY,            -- e.g. 'oracle-main'
  last_event_id  bigint NOT NULL DEFAULT 0,
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS session_summaries (
  session_id        uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  summary           text NOT NULL,
  topics            text[] NOT NULL DEFAULT '{}',
  files_touched     text[] NOT NULL DEFAULT '{}',
  through_event_id  bigint NOT NULL,          -- summary covers events.id <= this
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oracle_messages (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  target_user_id     uuid NOT NULL REFERENCES users(id),
  target_session_id  uuid REFERENCES sessions(id),   -- null = any live session of the user
  source_session_id  uuid REFERENCES sessions(id),   -- the session that prompted this
  kind               text NOT NULL,                  -- 'context' | 'warning' | 'question' | 'handoff'
  body               text NOT NULL,
  priority           smallint NOT NULL DEFAULT 0,
  created_at         timestamptz NOT NULL DEFAULT now(),
  delivered_at       timestamptz,
  acknowledged_at    timestamptz,
  metadata           jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS oracle_messages_pending_idx
  ON oracle_messages (target_user_id, created_at) WHERE delivered_at IS NULL;
