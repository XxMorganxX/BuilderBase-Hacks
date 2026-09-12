# Oracle Phase 1: Session Ingestion Gateway and Database

**Status:** ready for implementation.
**Written:** 2026-09-12, hackathon start, 4-hour total budget.
**Implementer:** Opus, autonomously, following `~/.claude/CLAUDE.md`.
**Principles:** `docs/PRINCIPLES.md` (P1..P10 referenced below).
**Working log:** `tasks/phase-1-ingestion-gateway.md`. Append as you go. Deviations from this plan go there first, then here.

---

## 0. Implementer brief (read this first)

- Deliver the thin end-to-end path (milestone M3 in section 10) before polishing anything. P9.
- Every decision in section 3 is made. Do not re-open them. If one proves wrong, log the deviation in `tasks/` and move on.
- The database and gateway run on a **separate machine** ("the server"). Its SSH credentials are in the existing `.env` (gitignored; note the file currently has a space before `=` on each line and will need cleaning up before any dotenv loader reads it). Develop against a local Postgres in Docker; deploy to the server at M4.
- Real session files for fixtures exist on this laptop: `~/.claude/projects/*/*.jsonl` (Claude Code) and `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (Codex). Copy a handful of lines, redact paths and content, commit under `fixtures/`.
- TDD the adapters and the ingest idempotency (global CLAUDE.md). Everything else can be verified with the curl script in section 11.
- No vendor name (`claude`, `codex`) appears outside `gateway/oracle_gateway/adapters/` and `shippers/`. P2.

## 1. Goal and acceptance

**Goal.** Any agent on any employee's machine can post its session to a central database, in a vendor-neutral format, idempotently, and a reader can page through everything in order.

**Acceptance** (demo at end of phase 1):

1. Postgres and gateway running on the server, reachable from two laptops.
2. Laptop A ships a live Claude Code session and laptop B ships a Codex session (or a second Claude Code user). Both appear under different users in `GET /v1/sessions`.
3. Re-running a shipper inserts zero new rows.
4. `GET /v1/events?after_id=0` returns everything in insertion order with canonical content blocks.
5. A full-text query on `events.content_text` finds a known prompt.
6. An arbitrary agent can `curl` canonical JSON to `POST /v1/ingest` and it lands.

## 2. Architecture

```
 employee laptop (many)                          server machine (one)
 +-----------------------------+                 +--------------------------------------+
 | Claude Code --+             |                 |  gateway (FastAPI, :8080)            |
 | Codex --------+- native     |   HTTP(S)       |   +- auth: bearer token -> user       |
 | Cursor -------+  jsonl      | --------------> |   +- adapters/{claude_code, codex}    |
 |        |                    |  POST /v1/      |   +- ingest: upsert session, append   |
 |  shipper (stdlib python)    |  ingest[/raw]   |   +- read API: sessions, events, inbox|
 |  tails files, posts new     |                 |              | asyncpg                |
 |  lines, remembers offsets   |                 |  postgres 16 (docker volume)         |
 +-----------------------------+                 +--------------------------------------+
                                                                 ^
                                        phase 2: Oracle agent polls /v1/events?after_id=N,
                                        writes session_summaries + oracle_messages.
                                        phase 3: a hook in each agent pulls /v1/inbox
                                        into the live session as context.
```

Components and contracts (P2, P7):

| Component | Responsibility | Interface | Swappable via |
|---|---|---|---|
| Shipper | Find native session files, post new lines | HTTP to gateway | Anything that can POST JSON |
| Adapter | Native lines to canonical batch | `SessionAdapter.parse(lines) -> ParsedBatch` | Registry dict keyed by `agent_kind` |
| Ingest service | Upsert session, append events, dedupe | `ingest(user_id, batch) -> IngestResult` | Only writer of the DB |
| Storage | Persist and page | SQL lives in `db.py` only | Edit `db.py` and `schema.sql` |
| Auth | Bearer token to user | `current_user(request) -> User` | Replace `auth.py` |

## 3. Decisions

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| D1 | Postgres 16 | Multiple writers over the network, JSONB for raw and canonical, built-in full-text search that phase 2 needs. | SQLite (single writer, local file). |
| D2 | Python 3.12, FastAPI, asyncpg, pydantic v2, `uv` | The Oracle agent (phase 2) uses the Anthropic Python SDK, so one language across the repo. FastAPI validates the canonical contract for free. | Node/Hono (fine, but splits the stack). |
| D3 | Adapters run **server-side** in the gateway; the shipper is dumb | One place to fix a format bug. Shipper is stdlib-only so any laptop runs it in ten seconds. The canonical endpoint still exists for agents that convert themselves. | Client-side conversion (N copies of adapter code). |
| D4 | Canonical content = Anthropic Messages content blocks (`text`, `thinking`, `tool_use`, `tool_result`) | Both Claude Code and Codex are tool-use transcripts. This shape loses nothing and is what the Oracle agent will feed to a model anyway. | Plain text (loses tool structure). |
| D5 | Idempotency key = `(session_id, external_event_id)` | Vendors give stable ids (Claude Code `uuid`, Codex `ordinal`). `ON CONFLICT DO NOTHING`. P4. | Content hashing (breaks on edits). |
| D6 | `events.id bigserial` is the global read cursor | Simplest correct-enough cursor for a poller. Not strictly commit-ordered under concurrent writers; fine at hackathon scale (section 15). | LISTEN/NOTIFY (extra moving part). |
| D7 | Store `raw` on every event | P3. Storage is cheap; re-projection is the safety net. | Drop raw. |
| D8 | Auth = one shared password (`ORACLE_PASSWORD`, default `oracle`) sent as a bearer token; identity declared per request in `X-Oracle-User` and auto-created | Nothing to provision or distribute, which removes a whole deployment step and a whole class of support question. Identity stays per person because Oracle routes to people (P5). | Per-user API keys (revised 2026-09-12: the provisioning cost was not buying anything on a trusted LAN). No identity at all (breaks P5 and the product). |
| D9 | Gateway and Postgres in one `docker-compose.yml` on the server | One command to deploy; volume-backed data. | Managed DB (network and time). |
| D10 | Shipper tails by byte offset with a local state file; Codex batches always include the `session_meta` header line | Avoids re-sending multi-MB files; the header makes each batch self-describing. Fallback if short on time: re-send the whole file, server dedupes. | Claude Code hooks only (not generic; can be added as a trigger later). |
| D11 | Phase-2 tables (`oracle_cursors`, `session_summaries`, `oracle_messages`) are created now | No migration tooling in a hackathon. Empty tables cost nothing and let phase 2 start immediately. | A migrations framework. |
| D12 | Sessions keyed `UNIQUE (user_id, agent_kind, external_session_id)` | Vendor ids are only unique per vendor per machine; the user scope makes them global. | Global uniqueness on vendor id. |
| D13 | Thinking blocks are stored | P3 says keep everything. They are cheap and useful for the Oracle's "what was this agent trying to do" summaries. Filterable by `event_type = 'thinking'`. | Drop thinking. |

## 4. Canonical contract

The only shape the gateway stores. The pydantic models in `gateway/oracle_gateway/models.py` are the source of truth (P8); this section must match them. If they diverge, fix the doc in the same commit.

### 4.1 Session

```json
{
  "external_id": "b816d83e-3a8f-447e-8280-0a4352bc8a0f",
  "agent_kind": "claude_code",
  "agent_version": "2.1.258",
  "workspace": "/Users/alice/dev/payments",
  "repo": "git@github.com:acme/payments.git",
  "branch": "feature/refunds",
  "title": "Add refund endpoint",
  "started_at": "2026-09-12T16:54:26.898Z",
  "ended_at": null,
  "metadata": {"hostname": "alice-mbp", "entrypoint": "cli"}
}
```

- `external_id` and `agent_kind` are required. `agent_kind` is a lowercase slug. Known values: `claude_code`, `codex`, `cursor`, `custom`.
- `title`: the vendor's own title if it publishes one (Claude Code writes an `ai-title` line), else the first real user prompt, truncated to 120 chars. A non-null incoming title replaces the stored one, because a vendor title arrives partway through a session.

### 4.2 Event

```json
{
  "external_id": "e0611aba-aed9-48c8-937f-bc142ba4c107",
  "parent_external_id": "a97ea1ee-9788-4a27-944a-c2c7ac8a65e1",
  "seq": 12,
  "type": "assistant_message",
  "role": "assistant",
  "content": [{"type": "text", "text": "I'll add the endpoint..."}],
  "text": "I'll add the endpoint...",
  "model": "claude-opus-5",
  "usage": {"input_tokens": 2, "output_tokens": 8550, "cache_read_input_tokens": 27948},
  "occurred_at": "2026-09-12T16:55:01.120Z",
  "metadata": {"is_sidechain": false, "request_id": "req_..."},
  "raw": {"...": "original native line, untouched"}
}
```

| Field | Required | Notes |
|---|---|---|
| `external_id` | yes | Vendor event id, or a stable derived id (see adapters). |
| `parent_external_id` | no | Vendor parent pointer if any. |
| `seq` | no | Per-session order. If absent the gateway assigns `max(seq)+1` for that session. |
| `type` | yes | `user_message`, `assistant_message`, `thinking`, `tool_call`, `tool_result`, `system`, `summary`, `other`. |
| `role` | no | `user`, `assistant`, `system`, `tool`. |
| `content` | yes | List of canonical blocks (4.3). May be empty. |
| `text` | no | Flattened text for search and prompts. Adapters always fill it; the gateway derives it from `content` when missing. |
| `model` | no | Vendor model id string. |
| `usage` | no | Vendor usage object, passed through as-is. |
| `occurred_at` | yes | ISO-8601 with timezone. |
| `metadata` | no | Vendor-specific fields worth querying: sidechain, meta, turn id, request id. |
| `raw` | no | Original line, untouched. Adapters always fill it. |

### 4.3 Content blocks

Anthropic Messages API shapes, used for every vendor:

- `{"type": "text", "text": "..."}`
- `{"type": "thinking", "thinking": "..."}`
- `{"type": "tool_use", "id": "...", "name": "Bash", "input": {...}}`
- `{"type": "tool_result", "tool_use_id": "...", "content": "..." | [blocks], "is_error": false}`
- `{"type": "image", ...}` passed through, never rendered.

`text` derivation rule: concatenate `text` blocks with newlines; for `thinking` use the thinking string; for `tool_use` use `"{name}({json input})"`; for `tool_result` use the string content. Truncate the result to 8 KB.

### 4.4 Ingest batch

```json
{"session": { ...4.1... }, "events": [ { ...4.2... }, ... ]}
```

Limits: 1000 events or 10 MB per request. `events` may be empty (this registers a session).

## 5. Database

Runs on the server. Apply `db/schema.sql` with `psql` or via the compose init mount. Copy the DDL below verbatim into `db/schema.sql`; that file is the source of truth once it exists (P8).

```sql
-- Oracle: session ingestion schema, phase 1. Postgres 16.
-- Apply: psql "$DATABASE_URL" -f db/schema.sql   (idempotent)

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- Identity (P5) --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text NOT NULL UNIQUE,
  display_name  text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- No api_keys table: authentication is one shared password held by the
-- gateway. A users row appears the first time someone ships under that
-- identity (decision D8).

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
  content             jsonb NOT NULL DEFAULT '[]'::jsonb,   -- canonical blocks (4.3)
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
```

**No seed step.** Users are created on first sight from the `X-Oracle-User` header, so there is nothing to run before a laptop can ship.

**docker-compose** (`db/docker-compose.yml`, run on the server):

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: oracle
      POSTGRES_USER: oracle
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - oracle_pgdata:/var/lib/postgresql/data
      - ./schema.sql:/docker-entrypoint-initdb.d/01_schema.sql:ro
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U oracle"]
      interval: 3s
      retries: 20
  gateway:
    build: ../gateway
    environment:
      DATABASE_URL: postgresql://oracle:${POSTGRES_PASSWORD}@db:5432/oracle
      ORACLE_GATEWAY_PORT: "8080"
      ORACLE_LOG_LEVEL: info
    ports: ["8080:8080"]
    depends_on:
      db:
        condition: service_healthy
volumes:
  oracle_pgdata: {}
```

**Environment variables** (`.env.example`, P8):

| Var | Where | Purpose |
|---|---|---|
| `POSTGRES_PASSWORD` | server | compose |
| `DATABASE_URL` | gateway, seed | asyncpg DSN |
| `ORACLE_GATEWAY_PORT` | gateway | default 8080 |
| `ORACLE_LOG_LEVEL` | gateway | default info |
| `ORACLE_PASSWORD` | gateway, shipper | the one shared password, default `oracle` |
| `ORACLE_GATEWAY_URL` | shipper | `http://SERVER:8080` |
| `ORACLE_USER` | shipper | the identity sent in `X-Oracle-User` |

## 6. Gateway API

Base `http://<server>:8080`. All `/v1/*` routes require `Authorization: Bearer <ORACLE_PASSWORD>`, and take an optional `X-Oracle-User` header naming the person the request belongs to (default `unattributed@oracle.local`). Errors are JSON `{"error": "...", "detail": ...}` with 400, 401, 413, 422, or 500.

| Method and path | Body | Returns | Notes |
|---|---|---|---|
| `GET /healthz` | none | `{"ok": true, "db": true}` | No auth. |
| `GET /v1/me` | none | current user | Password and identity sanity check. |
| `POST /v1/ingest` | canonical batch (4.4) | `{"session_id", "received", "inserted", "duplicates", "skipped"}` | The contract endpoint. |
| `POST /v1/ingest/raw/{agent_kind}` | `application/x-ndjson`, native lines | same as above | Runs the adapter, then the same ingest path. 400 if the adapter is unknown or cannot find the session header. |
| `GET /v1/sessions?user_id=&agent_kind=&since=&limit=50` | none | sessions without events | Ordered by `last_event_at desc`. |
| `GET /v1/sessions/{id}` | none | session with counts | |
| `GET /v1/sessions/{id}/events?after_seq=0&limit=500` | none | events ordered by `seq` | |
| `GET /v1/events?after_id=0&limit=500&types=` | none | events ordered by `id`, each with `session_id` and `user_id` | The Oracle cursor feed. |
| `GET /v1/inbox?session_id=` | none | undelivered `oracle_messages` for the user | Phase 3. Stub returning `[]` now. |

**Ingest semantics**, one transaction per batch:

1. Upsert `sessions` on `(user_id, agent_kind, external_session_id)`. Nullable header fields, `title` included, are only overwritten when the incoming value is non-null. `started_at = LEAST(existing, incoming)`.
2. Assign `seq` for events that lack one, starting at `max(seq)+1` for the session.
3. `INSERT ... ON CONFLICT (session_id, external_event_id) DO NOTHING RETURNING id`.
4. `UPDATE sessions SET last_event_at = GREATEST(last_event_at, max(occurred_at)), event_count = event_count + inserted, updated_at = now()`.
5. Return counts. Duplicates are success (P4).

## 7. Adapters

Interface (`gateway/oracle_gateway/adapters/base.py`):

```python
class ParsedBatch(BaseModel):
    session: SessionIn
    events: list[EventIn]
    skipped: int          # lines dropped as chrome or unparseable

class SessionAdapter(Protocol):
    agent_kind: str
    def parse(self, lines: Iterable[str]) -> ParsedBatch: ...
```

Registry in `adapters/__init__.py`: `ADAPTERS: dict[str, SessionAdapter]`. Rules: never raise on an unknown line type; count and continue. Always populate `raw`, `text`, `occurred_at`.

### 7.1 Claude Code

Files: `~/.claude/projects/<cwd-slug>/<sessionId>.jsonl`. One JSON object per line.
Observed top-level `type` values: `user`, `assistant`, `summary`, `attachment`, `message`, `mode`, `permission-mode`, `bridge-session`, `file-history-snapshot`, `last-prompt`, `atis-latch`. Ingest only `user`, `assistant`, `summary`. Everything else is chrome: drop and count.

Session mapping:

| Canonical | Source |
|---|---|
| `external_id` | `sessionId` (present on every user/assistant line) |
| `workspace` | `cwd` |
| `branch` | `gitBranch` |
| `agent_version` | `version` |
| `metadata.entrypoint` | `entrypoint` |
| `started_at` | min `timestamp` in the batch |
| `title` | the `aiTitle` field of the `ai-title` line when present; else the first `user` line that is neither `isMeta` nor injected context (see below), truncated to 120 chars |

Event mapping (one line = one event):

| Canonical | Source |
|---|---|
| `external_id` | `uuid` |
| `parent_external_id` | `parentUuid` |
| `occurred_at` | `timestamp` |
| `role` | `message.role` |
| `content` | `message.content`; if it is a string, wrap as one `text` block |
| `type` | `user` line: `tool_result` if any block is `tool_result`, else `user_message`. `assistant` line: `thinking` if every block is `thinking`; `tool_call` if any block is `tool_use`; else `assistant_message`. `summary` line: `summary`, content = one `text` block from the `summary` field. |
| `model` | `message.model` (assistant only) |
| `usage` | `message.usage` (assistant only, passed through) |
| `metadata` | `{is_sidechain, is_meta, prompt_id, request_id, api_block_index}` from `isSidechain`, `isMeta`, `promptId`, `requestId`, `apiBlockIndex` |

Note: Claude Code writes each API content block as its own line (`apiBlockIndex`), so a thinking line, a text line, and a tool_use line from one model turn are three events sharing `requestId`. Keep them separate; readers regroup by `metadata.request_id`. Lines with `isMeta: true` (slash-command echoes) are ingested and flagged, not dropped.

**Injected context.** Both vendors inject harness context into the transcript as user-role messages: Claude Code writes `<command-name>/model</command-name>` and `<local-command-stdout>...</local-command-stdout>`, Codex writes `<recommended_plugins>...</recommended_plugins>` and `<environment_context>`. Only some carry a vendor flag, so `adapters/base.is_injected_context` recognises the shape instead: an opening pseudo-tag that is closed later in the same text. These messages are still stored as events; they are only barred from becoming the session title. Run the check on the full message, never on the truncated title candidate, or the closing tag falls outside the window.

### 7.2 Codex

Files: `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session_id>.jsonl`.
Every line: `{"timestamp", "ordinal", "type", "payload"}`. Observed `type`: `session_meta`, `response_item`, `event_msg`, `turn_context`, `world_state`, `token_usage_record`.

Session comes from the `session_meta` line, which the shipper guarantees is in every batch (D10):

| Canonical | Source |
|---|---|
| `external_id` | `payload.session_id`, fallback `payload.id`, fallback the uuid in the file name |
| `workspace` | `payload.cwd` |
| `agent_version` | `payload.cli_version` |
| `started_at` | `payload.timestamp` |
| `metadata` | `{originator, source, model_provider}` |

Events come from `response_item` lines only. `turn_context.payload.turn_id` is carried forward into `metadata.turn_id`; the latest `world_state.payload.state.collaboration_mode.model` is carried forward into `model`. `event_msg` and `token_usage_record` lines are dropped and counted in v1 (TODO: attach usage to the preceding assistant event).

| `payload.type` | Canonical `type` | `role` | `content` |
|---|---|---|---|
| `message`, role `user` | `user_message` | user | `input_text` blocks become `text` blocks |
| `message`, role `assistant` | `assistant_message` | assistant | `output_text` blocks become `text` blocks |
| `message`, role `developer` | `system` | system | text blocks |
| `reasoning` | `thinking` | assistant | `payload.summary[].text` joined, else empty, as one `thinking` block |
| `function_call`, `custom_tool_call` | `tool_call` | assistant | one `tool_use` `{id: call_id, name, input: parsed arguments or input}` |
| `function_call_output`, `custom_tool_call_output` | `tool_result` | tool | one `tool_result` `{tool_use_id: call_id, content: output}` |

`external_id` = `payload.id` if present, else `"{session_id}:{ordinal}"`. `seq` = `ordinal`. `occurred_at` = line `timestamp`.

### 7.3 Adding another agent

New file in `adapters/`, one line in the registry, a fixture, a test. Nothing else changes (P2). Agents that cannot be file-tailed post canonical JSON to `/v1/ingest` directly.

## 8. Shipper

`shippers/oracle_shipper.py`, standard library only (`json`, `urllib.request`, `pathlib`, `time`, `argparse`). Runs on each laptop:

```
python3 shippers/oracle_shipper.py --gateway http://SERVER:8080 --password oracle --user you@example.com [--once] [--backfill] [--interval 2]
```

1. Discover files: `~/.claude/projects/**/*.jsonl` as `claude_code`; `~/.codex/sessions/**/rollout-*.jsonl` as `codex`. On startup ignore files not modified in the last 24 hours unless `--backfill`.
2. State file `~/.oracle/shipper_state.json`: `{path: {"offset": bytes, "header": "<session_meta line, codex only>"}}`.
3. Every interval, for each file whose size exceeds its offset: read new complete lines (never a partial trailing line), batch up to 500 lines, POST to `/v1/ingest/raw/{kind}` as `application/x-ndjson`. For codex, prepend the header line. On 2xx advance the offset. On failure log and retry next tick without advancing.
4. `--once`: single pass then exit. This is what a Claude Code `Stop` hook would call later.
5. Print one line per batch: `kind session_id received/inserted/duplicates`.

Fallback if the offset logic eats time: send the whole file every tick; the server dedupes (P4).

## 9. Repo layout

```
hackathon_oracle/
  README.md                       # what this is, how to run server and shipper
  .gitignore                      # exists
  .env                            # exists, gitignored, server ssh creds; add gateway vars
  .env.example
  docs/
    PRINCIPLES.md                 # exists
    PLAN.md                       # this file
  tasks/
    phase-1-ingestion-gateway.md  # exists; working log
  db/
    schema.sql
    docker-compose.yml
  gateway/
    pyproject.toml                # fastapi, uvicorn, asyncpg, pydantic>=2
    Dockerfile
    oracle_gateway/
      __init__.py
      config.py                   # all env-derived settings, read once
      __main__.py                 # python -m oracle_gateway, for the no-Docker path
      main.py                     # FastAPI app and routes only
      models.py                   # canonical pydantic models (4.x)
      db.py                       # asyncpg pool + every SQL statement
      auth.py                     # bearer -> user
      ingest.py                   # upsert session, append events, counters
      adapters/
        __init__.py               # ADAPTERS registry
        base.py                   # SessionAdapter protocol, ParsedBatch
        claude_code.py
        codex.py
    tests/
      test_claude_code_adapter.py
      test_codex_adapter.py
      test_ingest_idempotency.py  # needs a DB; skip if DATABASE_URL unset
  shippers/
    oracle_shipper.py
    README.md
  fixtures/
    claude_code_sample.jsonl      # redacted real lines, ~20
    codex_sample.jsonl
```

## 10. Build order and timeboxes

Clock starts when Opus begins. Phase-1 budget: 2h15m, leaving about 1h30m for phase 2.

| M | Window | Deliverable | Done when |
|---|---|---|---|
| M0 | 0:00-0:10 | `git init`, `.env.example`, `README.md`, `db/schema.sql`, `db/docker-compose.yml`; `docker compose up db` locally | `psql` lists the tables |
| M1 | 0:10-0:45 | Gateway: `config.py`, `db.py`, `models.py`, `auth.py`, `ingest.py`, `main.py` with `/healthz`, `/v1/me`, `/v1/ingest`, `/v1/sessions*`, `/v1/events` | curl posts a canonical batch twice; second response says `inserted: 0` |
| M2 | 0:45-1:15 | Claude Code adapter, TDD on the fixture; `/v1/ingest/raw/claude_code` | tests pass; the raw fixture lands with the right event types |
| M3 | 1:15-1:35 | Shipper; run on this laptop against the local gateway with the live session | this session's events show up in `GET /v1/events` |
| M4 | 1:35-1:55 | Deploy: clone on the server, `docker compose up -d`, point the shipper at it | laptop to server round trip works |
| M5 | 1:55-2:15 | Codex adapter, TDD on the fixture; a second user or laptop ships | acceptance items 1-6 |

If behind at M3: skip Codex, demo with two Claude Code users, and note it in `tasks/`.

## 11. Verification script

```bash
export G=http://SERVER:8080 T=oracle U=you@example.com
curl -s $G/healthz
curl -s -H "Authorization: Bearer $T" $G/v1/me
# canonical ingest, twice; second must report inserted 0
cat > /tmp/batch.json <<'JSON'
{"session":{"external_id":"demo-1","agent_kind":"custom","workspace":"/tmp/demo"},
 "events":[{"external_id":"e1","type":"user_message","role":"user",
            "content":[{"type":"text","text":"hello oracle"}],
            "occurred_at":"2026-09-12T18:00:00Z"}]}
JSON
curl -s -H "Authorization: Bearer $T" -H 'Content-Type: application/json' --data @/tmp/batch.json $G/v1/ingest
curl -s -H "Authorization: Bearer $T" -H 'Content-Type: application/json' --data @/tmp/batch.json $G/v1/ingest
# raw ingest of a real file
f=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
curl -s -H "Authorization: Bearer $T" -H 'Content-Type: application/x-ndjson' --data-binary @"$f" $G/v1/ingest/raw/claude_code
curl -s -H "Authorization: Bearer $T" "$G/v1/sessions?limit=5"
curl -s -H "Authorization: Bearer $T" "$G/v1/events?after_id=0&limit=3"
# full text
psql "$DATABASE_URL" -c "select session_id, left(content_text,60) from events where to_tsvector('english', content_text) @@ plainto_tsquery('hello oracle');"
```

## 11a. Deviations from this plan, as built

Recorded here because the plan is the spec and the code is now ahead of it in these places. Narrative and reasoning: `tasks/phase-1-ingestion-gateway.md`.

| # | Planned | Built | Why |
|---|---|---|---|
| 1 | `db/seed.py` | `gateway/oracle_gateway/seed.py`, run as `python -m oracle_gateway.seed` | It needs asyncpg and the auth hash function, both of which live in the gateway package. Running it inside the gateway container means the server needs no second Python environment. |
| 2 | Title = first real user prompt | Vendor title (`ai-title`) first, then first non-injected user prompt | Claude Code publishes its own generated session title. It is better than anything inferable, and it is what makes a session list readable. |
| 3 | Gateway only overwrites a null title | A non-null incoming title wins | `ai-title` appears several turns into a session, so first-write-wins pinned whatever chrome the first batch saw. |
| 4 | `isMeta` identifies slash-command chrome | Shared `is_injected_context` shape check | Claude Code does not flag the `<command-name>` line itself, and Codex has no equivalent flag at all. |
| 5 | (not planned) | `oracle_gateway/__main__.py` | Gives the no-Docker fallback in the deployment runbook a real entry point, and makes `ORACLE_GATEWAY_PORT` mean something outside compose. |
| 6 | `ORACLE_GATEWAY_PORT` passed into the container | Host-side port mapping only; the container is fixed on 8080 | It was doing two jobs and would have silently disagreed with itself if changed. |
| 7 | Per-user API keys in an `api_keys` table, issued by a seed script | One shared password plus an `X-Oracle-User` header; users auto-create; `api_keys` and the seed script are gone | Requested after the first build. Provisioning and distributing keys bought nothing on a trusted LAN, and it cost a deployment step. Identity had to stay, so it moved to a header: authentication and identity are now separate concerns, which is the honest shape anyway. |

Observed but needing no change: Claude Code emits many more chrome line types than the four seen at planning time (`ai-title`, `queue-operation`, `atis-latch`, `relocated`, `worktree-state`, `file-history-delta`, `pr-link`, `cost-state`, `system`). The adapter allow-lists conversation types rather than deny-listing chrome, so each new one is counted and dropped without a code change. That is decision D7 and principle P3 earning their keep on day one.

## 12. Phase 2 and 3 sketch (what phase 1 must not block)

**Phase 2, the Oracle agent.** A target agent per the global CLAUDE.md, so `agents/oracle/` gets the six component docs and a `config.py` before any code. Sketch:

- *Model:* `claude-opus-5`, adaptive thinking (omit the `thinking` param or pass `{type: "adaptive"}`), streaming, server-side refusal fallbacks on by default (`betas: ["server-side-fallback-2026-07-01"]`, `fallbacks: "default"`). Behind a `ModelProvider` interface (P7).
- *Tool registry:* `read_events(after_id, limit)`, `read_session(id)`, `upsert_summary(session_id, ...)`, `post_message(target_user, target_session, kind, body)`. All via the gateway API, never raw SQL.
- *Control loop:* poll `/v1/events?after_id=` every N seconds from `oracle_cursors['oracle-main']`; group new events by session; refresh `session_summaries`; run a cross-session relevance pass over sessions active in the last hour (same workspace or repo, overlapping files, contradictory decisions); write `oracle_messages`; advance the cursor.
- *State:* per-tick in memory. *Memory:* `session_summaries` and `oracle_cursors`. *Logging:* one JSON line per tick with events read, sessions touched, messages written, tokens used.

**Phase 3, the channel.** `GET /v1/inbox?session_id=` returns undelivered messages and marks them delivered. For Claude Code, a `UserPromptSubmit` hook (which receives `session_id` on stdin and whose stdout is injected as context) runs `curl` against the inbox and prints the messages. That gives cross-agent communication with zero changes to the agent. Codex and others get the same via a small MCP tool or an `AGENTS.md` instruction to poll.

## 13. Open questions (non-blocking; defaults chosen, change if wrong)

1. **Server machine OS and Docker.** Assumed Linux with Docker Compose available. If not, fall back to a native `apt install postgresql-16` and run the gateway with `uv run uvicorn` under `nohup`.
2. **Which agents are in the demo.** Assumed Claude Code plus Codex. Cursor and others are out unless someone at the table already has a session file to look at.
3. **TLS.** Assumed plain HTTP on the LAN for the hackathon. P10.

## 14. Out of scope for phase 1

- The Oracle agent itself (phase 2) and delivery (phase 3), beyond the reserved tables and the inbox stub.
- Secret redaction, retention, per-user visibility, SSO.
- Web UI.
- Handling `token_usage_record` lines from Codex; attachments and images beyond pass-through.

## 15. Risks

| Risk | Mitigation |
|---|---|
| Server machine has no Docker or a firewall blocks 8080/5432 | Check at M0 with one SSH command before anything else. Native install fallback in section 13. |
| Claude Code JSONL contains very large tool results (MBs) | 10 MB request cap; shipper batches by 500 lines; `content_text` truncated to 8 KB. Raw still stored. |
| `bigserial` cursor skips an in-flight insert under concurrent writers | Oracle re-reads with `after_id = last_seen - 100` on each tick and dedupes in memory. Note in phase-2 docs. |
| Offset-tailing bugs eat time | D10 fallback: re-send whole files, server dedupes. |
| A vendor changes its line format mid-hackathon | Raw is stored (P3); re-project later. Adapters never raise. |
