# Oracle

Every engineer runs coding agents. Each agent sees exactly one session: one person, one repo, one task. It knows nothing about the other sessions running in the building that touch the same module, hit the same bug, or make a decision that quietly invalidates someone else's plan.

Oracle is the mediator that sees all of them. It ingests every agent session in the organization into one store, and will route what matters back into the sessions that need it.

Three layers, built in order:

1. **Substrate** — a gateway and database any agent can post its session to. **This is what exists today.**
2. **Observer** — the Oracle agent reads the stream, summarizes sessions, detects cross-session relevance.
3. **Channel** — delivery of Oracle's findings into a live agent session as context.

This directory is the **substrate**: the gateway and database. It lives at `oracle-gateway/` in the [BuilderBase-Hacks](https://github.com/XxMorganxX/BuilderBase-Hacks) monorepo. The sibling `Oracle/` directory there is `oracle-inbox`, a separate component by another author that handles layer 3, delivering Oracle's messages back into a live Claude Code session. The two meet at this service's HTTP API.

## Where things are

| Path | What it is |
|---|---|
| `docs/PRINCIPLES.md` | The constitution. Ten principles every decision traces back to. |
| `docs/PLAN.md` | The phase-1 spec: canonical contract, schema, API, adapter mappings. |
| `docs/SERVER-DEPLOYMENT.md` | **Runbook for the machine that hosts this.** Hand it to whoever sets up the server. |
| `db/schema.sql` | The database. Source of truth, idempotent. |
| `gateway/` | The ingestion service. See `gateway/README.md`. |
| `shippers/` | Getting sessions off a laptop. See `shippers/README.md`. |
| `fixtures/` | Redacted real session lines, used by the adapter tests. |
| `tasks/` | Working log: how the implementation actually unfolded. |

## What works today

Any agent on any laptop can post a session and it lands in Postgres, de-duplicated, in a vendor-neutral shape:

- **Two native formats ingest as-is.** Claude Code JSONL and Codex rollout files are converted server-side. Adding a third agent is one module and one registry entry.
- **Any other agent can post canonical JSON** to `/v1/ingest` without an adapter.
- **No key to provision.** One shared password lets a laptop write; a header says who is writing. Users appear the first time they ship.
- **Re-sending is free.** Ship the same file a hundred times; the second through hundredth write nothing.
- **Nothing is lost.** Every event keeps its original vendor payload alongside the canonical projection.
- **Everything is readable in order.** A global cursor feed is what the phase-2 Oracle agent will poll.

## Run it locally

```bash
docker run -d --name oracle-db -e POSTGRES_DB=oracle -e POSTGRES_USER=oracle \
  -e POSTGRES_PASSWORD=oracle -p 55432:5432 postgres:16
export DATABASE_URL=postgresql://oracle:oracle@localhost:55432/oracle
psql "$DATABASE_URL" -f db/schema.sql

cd gateway
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q                       # 57 tests
.venv/bin/python -m oracle_gateway        # http://localhost:8080
```

Then ship a session you already have on disk:

```bash
f=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
curl -s -H "Authorization: Bearer oracle" -H "X-Oracle-User: you@example.com" \
     -H 'Content-Type: application/x-ndjson' \
     --data-binary @"$f" http://localhost:8080/v1/ingest/raw/claude_code
```

## Deploy it

`docs/SERVER-DEPLOYMENT.md`, start to finish, on the server machine.

## A warning

This ingests source code, file paths, tool output, and whatever secrets an agent happened to see. There is no redaction, no TLS, and no per-user visibility: the one password reads and writes everything, and anyone holding it can claim to be anyone. That is a deliberate, documented debt for a hackathon on a trusted network, listed as principle P10. Read `docs/SERVER-DEPLOYMENT.md` section 13 before pointing it at anything real.
