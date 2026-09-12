# Task: Phase 1, session ingestion gateway and database

**Goal:** any agent (Claude Code, Codex, custom) on any employee's laptop can post its session to a central Postgres on a separate server, idempotently, in a vendor-neutral format. Spec: `docs/PLAN.md`. Principles: `docs/PRINCIPLES.md`.

**Approach:** Python/FastAPI gateway with server-side adapters, Postgres 16 in Docker on the server, a stdlib-only shipper on each laptop. Decisions D1-D13 are in the plan; do not duplicate them here, log deviations.

## Progress log

### 2026-09-12 (planning session, Fable)
- Inspected real session files on this laptop to ground the adapter spec:
  - Claude Code: `~/.claude/projects/<slug>/<session>.jsonl`; one line per API content block; `user`/`assistant`/`summary` are the conversation, the rest is chrome.
  - Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`; `{timestamp, ordinal, type, payload}`; `session_meta` header line, `response_item` payloads carry messages, reasoning, and tool calls.
- Wrote `docs/PRINCIPLES.md`, `docs/PLAN.md`, this file, and `.gitignore` (the existing `.env` holds server SSH credentials in plaintext).
- Handed off to Opus for implementation starting at M0.

### 2026-09-12 (implementation session, Opus)

Built M0 through M2 plus the deployment runbook. Milestones M3 (tailing shipper) and M4 (deploy to the real server) are not done; the curl recipe in `shippers/README.md` covers shipping in the meantime, and `docs/SERVER-DEPLOYMENT.md` is what M4 executes.

**What exists:** `db/schema.sql`, `db/docker-compose.yml`, the `gateway/` package (config, models, db, auth, ingest, seed, `__main__`, main, two adapters), 45 tests, redacted fixtures for both vendors, and four docs (root README, gateway README, shippers README, server runbook).

**Verified, not assumed.** Local Postgres in Docker, schema applied twice to prove idempotency, then the full compose stack built and run on ports 55433/8081. Through the deployed stack: two real Claude Code sessions and two real Codex sessions from this laptop, plus a re-ship of each showing `inserted: 0`. The no-Docker path (`python -m oracle_gateway`) was started and health-checked too, because the runbook offers it as a fallback and an untested fallback is a trap.

**Decisions taken during implementation** (the table in `docs/PLAN.md` section 11a is the summary; this is the reasoning):

- *Seed moved into the gateway package.* Planned as `db/seed.py`, but it needs asyncpg and the same token hashing as `auth.py`. Inside the package it runs as `docker compose exec gateway python -m oracle_gateway.seed` with no second Python environment on the server. One fewer thing to install is worth a lot in a runbook someone else executes.
- *Seq assignment moved inside the ingest transaction.* Originally a separate `next_seq()` call before the transaction, which would let two concurrent batches for one session claim the same starting number. It now reads `max(seq)` on the same connection inside the same transaction.
- *Titles turned out to be the interesting problem.* Three bugs in a row, each found by running real data through, not by reading the spec:
  1. The first user line of a Claude Code session is usually a slash-command echo. `isMeta` does not catch it, because Claude Code flags the caveat line but not the `<command-name>` line.
  2. Codex has the same problem with a different shape: `<recommended_plugins>` and `<environment_context>` arrive as ordinary user-role messages.
  3. The fix for both, a shape check for a closed pseudo-tag, silently failed on real files because the candidate was truncated to 120 characters *before* the check, cutting off the closing tag. The unit test passed because the fixture was short. Fixed by checking the full message and truncating after, with a test that uses a block longer than the limit.

  Along the way: Claude Code publishes its own `ai-title` line, which is better than anything inferable. Session lists now read like `Agent session database and Oracle mediator` instead of `<command-name>/model</command-name>`. That one line is most of what makes the phase-2 relevance pass tractable.
- *A non-null title now replaces the stored one.* Consequence of the above: `ai-title` shows up several turns into a session, so the planned first-write-wins rule pinned the chrome forever.
- *Reality check on Claude Code line types.* Planning saw four chrome types; the 40 most recent local sessions contain fourteen (`ai-title`, `queue-operation`, `atis-latch`, `relocated`, `worktree-state`, `file-history-delta`, `pr-link`, `cost-state`, `system`, and more). Nothing needed changing, because the adapter allow-lists conversation types instead of deny-listing chrome. Worth noting as evidence the allow-list was the right call.

**Not done, deliberately:** the tailing shipper (M3), deployment to the actual server (M4), and the phase-2 Oracle agent. No secret redaction, no TLS, no visibility rules; P10 debt, restated in section 13 of the runbook so whoever deploys it reads it.

## Open questions
- See `docs/PLAN.md` section 13. None are blocking.

## Out of scope
- See `docs/PLAN.md` section 14.
