# Oracle: Foundational Principles

**Status:** v1, written 2026-09-12 at hackathon start.
This is the project's constitution. `docs/PLAN.md` is the phase-1 execution plan derived from it. Every implementation choice should be traceable to a principle here. If a decision contradicts one, either the decision or the principle changes, and the change is logged in the decision record at the bottom.

## What Oracle is

Every engineer in a company now runs AI coding agents: Claude Code, Codex, Cursor, and whatever ships next month. Each agent sees exactly one session: one person, one repo, one task. It knows nothing about the twenty other sessions running in the building that touch the same module, hit the same bug, or make a decision that quietly invalidates someone else's plan.

Oracle is the mediator that sees all of them. It ingests every agent session in the organization into one store, watches the stream, and routes what matters back into the sessions that need it. An engineer's agent should be able to learn "someone on your team refactored this module an hour ago, here is what changed and why" without the engineer ever relaying it.

Three layers, built in order:

1. **Substrate (phase 1, this hackathon's first deliverable).** A gateway and database that any agent from any vendor can post its session to. This document and the plan are mostly about this layer.
2. **Observer (phase 2).** The Oracle agent reads the stream, summarizes sessions, and detects cross-session relevance.
3. **Channel (phase 3).** A delivery path that puts Oracle's findings into a live agent session as context.

## Principles

### P1. The session log is the substrate. Everything else is a reader.
The append-only event log is the single fact base. Oracle, dashboards, search, and every future feature are readers of that log. They never mutate it. Derived data (summaries, observations, messages) lives in its own tables and can be rebuilt from the log at any time.

*Consequence:* `events` is append-only. No `UPDATE`, no `DELETE` in normal operation. `sessions` is the one mutable header row (last-seen, counts, title), and even that is derivable from events.

### P2. The core is agent-agnostic. Vendor formats stop at the adapter.
The gateway and database know exactly one format: the canonical session/event contract in `docs/PLAN.md` section 4. Claude Code's JSONL, Codex's rollout files, and whatever Cursor writes are converted by adapters that implement one interface. Adding an agent means adding an adapter. It never means touching the schema or the ingest path.

*Consequence:* there is a `SessionAdapter` interface and a registry keyed by `agent_kind`. Nothing outside `adapters/` mentions a vendor name.

### P3. Keep the raw, project the canonical.
Every event stores the original native payload alongside the canonical projection. The canonical form is what we query. The raw form is what we trust. When an adapter is wrong or the canonical model grows, we re-project from raw on the server instead of asking every laptop to re-ship.

### P4. Ingestion is idempotent. Shippers can be dumb.
Clients may crash, retry, or re-send a whole file. The gateway deduplicates on the vendor's own event id (or a stable derived id) and treats "already have it" as success. This is what lets the client be a few dozen lines of standard-library code that anyone can run in ten seconds.

### P5. Identity is the person, not the machine.
Every session belongs to an employee. Two sessions on two laptops belonging to the same person are that person's work. Oracle routes to people and their live sessions, never to IP addresses.

Authentication and identity are separate questions, and only the first is allowed to get simpler. A shipper proves it may write (currently one shared password) and separately declares who it is. Weakening the proof is a deployment choice; dropping the declaration would remove the thing Oracle exists to use.

### P6. Oracle observes and speaks. It never edits history.
Oracle writes its own outputs (summaries, messages to sessions) into dedicated tables. It cannot alter or delete events. If Oracle is wrong, its messages are wrong. The record stays right.

### P7. Component independence.
Inherits the global rule from `~/.claude/CLAUDE.md`. Storage, model provider, adapter set, and delivery mechanism each sit behind an interface and are swapped by configuration. The gateway does not import `anthropic`. The Oracle agent does not import `asyncpg` directly; it reads through the gateway API or a storage interface. The shipper does not know Postgres exists.

### P8. One source of truth per thing.
- Schema: `db/schema.sql`.
- Canonical contract: the pydantic models in the gateway, documented in `docs/PLAN.md` section 4.
- Configuration: the env vars listed in `.env.example`, read once in `config.py`.
- Prompts and tunables for the Oracle agent: `agents/oracle/config.py` (phase 2).

If you find yourself grepping for a value, that value is in the wrong place.

### P9. Hackathon discipline: end-to-end first, then wider.
A thin path that works from a real laptop session to a row in the remote database beats a complete gateway that has never been hit. Every milestone in the plan ends in something demoable. Stub, do not skip, and log every stub in `tasks/`.

### P10. Privacy is a known debt, declared up front.
Sessions contain source code, file paths, tool output, and sometimes secrets. For the hackathon: trusted LAN, a small consenting group, no external exposure, one shared password that anyone can use to claim any identity. Before any real deployment: secret redaction at the shipper, per-user visibility controls, retention limits, TLS, and real authentication. These are listed as gaps in the plan, not forgotten.

## Vocabulary

| Term | Meaning |
|---|---|
| **Agent** | A vendor coding assistant running on an employee's machine: Claude Code, Codex, Cursor, custom. Identified by `agent_kind`. |
| **Session** | One conversation or run of one agent for one user. Has the vendor's own id (`external_session_id`) and an Oracle id. |
| **Event** | One immutable line of a session: user message, assistant message, thinking block, tool call, tool result, summary, or system note. |
| **Canonical format** | The vendor-neutral JSON shape for sessions and events. The only thing the gateway stores. |
| **Adapter** | Code that turns one vendor's native session format into canonical. One per `agent_kind`. |
| **Shipper** | The client on an employee's machine that posts session data to the gateway. |
| **Gateway** | The HTTP service that authenticates shippers, runs adapters, and writes to the database. |
| **Oracle (agent)** | The phase-2 reader that summarizes, correlates, and produces messages. |
| **Oracle message** | A phase-2 output addressed to a user or a live session. |
| **Channel / inbox** | The phase-3 path by which an Oracle message reaches an agent session as context. |

## Non-goals for the hackathon

- Real-time transport (WebSockets, server push). Polling every few seconds is fine.
- Multi-tenant organizations. One org.
- A UI beyond read endpoints and, if time allows, a single status page.
- Perfect fidelity for every vendor line type. Ingest the conversation; drop UI chrome.
- Authentication beyond bearer tokens.

## Decision record

Changes to these principles are logged here with a date and a reason.

- 2026-09-12: v1 written at hackathon start.
- 2026-09-12: P5 split into authentication and identity, after per-user API keys were replaced by one shared password plus a declared identity header. The principle is unchanged; what changed is that the password no longer carries the identity, so the header has to.
