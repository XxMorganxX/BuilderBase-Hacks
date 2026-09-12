# Task: Agenda Doc Knowledge Base + MCP surface

## Goal

Managers submit **agenda docs** — detailed, high-level statements of what a department or
product team is aiming for. This task builds:

1. A very simple, queryable knowledge base of those docs.
2. An MCP server so an agent can *select* the right agenda doc for whatever company
   project it is currently discussing ("what is Product #1 aiming for?" → that product's
   agenda doc).

## Approach

Markdown files with YAML frontmatter, one file per agenda doc, in `kb/agenda/`.
No database, no embeddings, no build step — the filesystem *is* the KB, so a manager
submits a doc by dropping a `.md` file in a directory and the agent sees it on next load.

Retrieval is lexical scoring over weighted frontmatter fields plus the body. For a KB of
tens-to-hundreds of docs this is accurate enough and has zero infrastructure.

## Decisions

| Decision | Rationale |
|---|---|
| Markdown + YAML frontmatter | Managers already write prose; frontmatter is the only structure we impose. Human-editable, diffable, reviewable in a PR. |
| Filesystem as the store | "Very, very simplistic" was the ask. No migrations, no server to run, no sync job. |
| Lexical scoring, not embeddings | Deterministic, debuggable, no API key, no index to rebuild. Project names/aliases are near-exact matches in practice. |
| `aliases` field on every doc | Agents and humans say "product 1", "Atlas", "the checkout thing". Aliases map all of those to one doc without fuzzy-matching guesswork. |
| Store behind an `AgendaStore` protocol | The MCP layer never touches the filesystem. Swapping Markdown for Notion/Confluence/Postgres later is one new class + one wiring line in `config.py`. |
| Three tools: list / find / get | `list` to browse, `find` to select, `get` to read in full. Deliberately small action space. |
| Python + `mcp` SDK via `uv` | System Python is 3.9; the MCP SDK needs ≥3.10. `uv run` pins 3.11 without touching the machine's Python. |

## Progress log

### 2026-09-12 — scaffolding
- Created `kb/agenda/` (the KB), `src/agenda_kb/` (loader + MCP surface), `tests/`.
- Wrote `kb/SCHEMA.md` as the contract managers write against.

### 2026-09-12 — tests first
- `tests/test_store.py`, `tests/test_search.py` written before implementation (red).

### 2026-09-12 — implementation
- `config.py` — single source of truth for paths, field weights, limits, tool descriptions.
- `store.py` — `AgendaStore` protocol + `MarkdownAgendaStore`.
- `search.py` — weighted token scoring.
- `server.py` — MCP stdio surface, thin wrapper over the store.

## Open questions

- Should stale docs (past `period`, `status: archived`) be excluded from `find` by default?
  Currently they are returned but flagged, so the agent can say "this is last quarter's".
- No auth/permissions model. Every agenda doc is visible to any agent with the server.

## Out of scope

- Write path (agents submitting/editing agenda docs) — read-only by design for v1.
- Embeddings / semantic search.
- Multi-tenant or per-department access control.
