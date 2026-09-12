# Task: MongoDB backend for the agenda KB

## Goal

Stand up a MongoDB the agenda docs live in, so a RAG system can pull them without parsing
Markdown. Two consumers, and they want different things:

1. **This project's MCP server** — needs a store it can list, get, and search through. It must
   not learn that Mongo exists.
2. **An external RAG system** — will query Mongo directly, in its own language, with its own
   retrieval. It needs a sane document shape and the right indexes, not a Python API.

Both are served by the same collection. Neither is served by making the other's job weird.

## Approach

`MongoAgendaStore` implements the existing `AgendaStore` protocol alongside
`MarkdownAgendaStore`. The Markdown files stay the source of truth — a manager still submits a
doc by dropping a file in `kb/agenda/` — and an idempotent ingest command projects them into
Mongo. Mongo is a read model, not the system of record.

That direction matters. Making Mongo authoritative would mean building a write path, an editing
surface, and a migration story for a KB whose whole premise is "the filesystem is the KB."
Making it a projection means ingest is re-runnable, the DB can be dropped and rebuilt in a
second, and nothing is lost if the container is deleted.

Local Mongo runs from `docker-compose.yml`. The connection string is a config value, so the same
code points at Atlas by setting `AGENDA_MONGO_URI` — no code change, which is the component
independence rule doing its job.

## Decisions

| Decision | Rationale |
|---|---|
| Mongo as a projection of the Markdown files, not the source of truth | Keeps the "submit a doc by adding a file" premise intact and makes ingest safely re-runnable. Rebuilding the DB from scratch is one command. |
| `_id` is the doc's `id` | The doc id is already unique and kebab-case. Using it as `_id` gets uniqueness enforced by the server, makes `get` a primary-key lookup, and makes ingest a natural upsert. A separate ObjectId would buy nothing. |
| Retrieval stays in the shared scorer, not `$text` | The protocol's ranking must not change with the backend. Both stores load docs and rank them through the same `search.py`, so `find_agenda_doc` returns the same order either way — which is a property worth being able to test. |
| A `$text` index is still created | The external RAG system queries Mongo directly and should not have to reimplement scoring in Python. The index is for *them*; the protocol path does not use it. Weights mirror `config.FIELD_WEIGHTS`. |
| `aliases` indexed, and resolvable in one query | `get_doc("iphone duo")` has to work. A multikey index on a normalized alias array makes id-or-alias resolution a single indexed lookup instead of a scan. |
| `mongomock` for unit tests, real server for integration | Unit tests must run with no daemon, on any machine, in milliseconds. The integration test skips itself when nothing is listening on the configured URI rather than failing. |
| No embeddings or vector search in v1 | No API key, no provider dependency, and the docs are ~70 lines each — whole-doc retrieval is the right granularity for this corpus. The seam for it is noted in Open questions. |
| Local Mongo bound to `127.0.0.1`, no auth | It is an unauthenticated dev database. Binding to localhost means "no auth" stays a local convenience rather than a network-exposed one. Real deployments set a URI with credentials. |

## Progress log

### 2026-09-12 — environment
No `mongod`, `mongosh`, or Mongo image on the machine; Docker Desktop running. Chose a
`docker-compose.yml` with `mongo:7` over a Homebrew install: nothing to uninstall, and
`docker compose down -v` is a complete cleanup.

Port 27017 was already taken — a sibling hackathon project has an Atlas-local Mongo there for
its scoped-memory KB. Published ours on **27018** instead rather than reusing theirs: two
projects sharing one container means either team's `docker compose down -v` takes out the
other's database. The URI is config, so pointing at theirs, or at real Atlas, is one env var.

### 2026-09-12 — tests first
`tests/test_mongo_store.py` written before the implementation, against `mongomock` (21 tests,
no server needed). `tests/test_mongo_integration.py` covers what mongomock cannot — that the
`$text` index is real and queryable, and that alias lookup uses `IXSCAN` rather than a
collection scan. It skips itself when nothing answers on the configured URI, and runs against a
throwaway `agenda_kb_pytest` database so a test run can never touch the real KB.

The load-bearing test is `test_search_ranks_identically_to_the_markdown_store`: same query, same
doc ids, same scores from both backends. Without it, "swap the store" silently becomes "change
the answers the agent gives."

### 2026-09-12 — implementation
- `config.py` — `STORE_BACKEND`, `MONGO_URI` / `MONGO_DB` / `MONGO_COLLECTION`, timeout, pinned
  index names, and `MONGO_TEXT_WEIGHTS`. All env-overridable; no connection detail anywhere else.
- `mongo_store.py` — `MongoAgendaStore` plus `to_document` / `document_to_doc` / `lookup_keys_for`.
  Holds no cache, so `reload()` is a documented no-op and a doc ingested by another process is
  served on the next question.
- `ingest.py` — `agenda-kb-ingest`. Upserts every doc by id, deletes documents whose Markdown file
  is gone, ensures both indexes. A dead database exits 1 with the command to start one, not a
  traceback.
- `store.py` — `create_store()` now branches on `STORE_BACKEND`; unknown values warn and fall back
  to Markdown. `server.py` was not touched, which was the whole point.

### 2026-09-12 — verified end to end
- `65 passed` (36 pre-existing, 21 mongomock, 8 real-server integration). Nothing pre-existing
  changed behaviour.
- `uv run agenda-kb-ingest` → 3 docs into `agenda_kb.agenda_docs`. Both indexes present, text
  weights as configured.
- `$text` straight from the collection, no Python: "lossless audio latency" → `airpods-link-protocol`
  (47.4), "fold transition compatibility" → `iphone-duo-ios` (30.7), "window tiling Rosetta" →
  `macos-platform` (5.7). Correct doc first each time; that is the path the RAG system takes.
- `AGENDA_STORE_BACKEND=mongo` through the MCP formatters: catalogue lists all three, "what is the
  airpods team aiming for?" ranks `airpods-link-protocol` first at 49.0, and `get_agenda_doc("the
  fold")` resolves the alias to `iphone-duo-ios`.

## How to use it

```bash
docker compose up -d                       # start MongoDB on 127.0.0.1:27018
uv run agenda-kb-ingest                    # sync kb/agenda/*.md into the database
AGENDA_STORE_BACKEND=mongo uv run agenda-kb   # run the MCP server off Mongo
```

Re-run `agenda-kb-ingest` after any change to `kb/agenda/`; it is a sync, not an append.

For the RAG system, the contract is the collection `agenda_kb.agenda_docs`:

- `_id` and `id` — the doc id, e.g. `macos-platform`
- frontmatter as flat fields: `title`, `project`, `department`, `team`, `owner`, `status`,
  `period`, `updated`, plus `aliases` and `tags` as arrays
- `summary` — the doc's mission line, for cheap candidate display
- `body` — the full Markdown, verbatim, which is what a RAG answer should quote from
- `lookup_keys` — normalized names the doc answers to; indexed, for resolving "iphone duo"
- `source_path` — the Markdown file this document was projected from
- the `agenda_text` index for `{"$text": {"$search": ...}}`, weighted per `MONGO_TEXT_WEIGHTS`

Point at a different cluster with `AGENDA_MONGO_URI`; nothing in the package changes.

## Open questions

- **Chunking.** Each doc is stored whole (~5KB), which suits this corpus: an agenda doc's sections
  only make sense together, and "explicitly not doing" is exactly the part a naive chunker drops.
  If the RAG system wants passage-level citations, the natural split is per `##` section into a
  `sections` array, which can be added at ingest without touching the store.
- **Vector search.** Deliberately absent — no API key, no provider dependency, and lexical
  retrieval already puts the right doc first on every query tried. The seam is a `embedding` field
  written at ingest plus an Atlas vector index; the machine already has an
  `mongodb/mongodb-atlas-local` image for that, and the sibling scoped-memory project is using it.
- **Ingest is manual.** No watcher, no cron. A doc edited in `kb/agenda/` is stale in Mongo until
  someone re-runs the command. Fine while the Markdown store is the default backend; worth a file
  watcher if Mongo ever becomes the primary read path.
- **No auth on the local instance.** Bound to `127.0.0.1`, which is the mitigation. A shared or
  remote deployment needs a URI with credentials, and `.env` is already gitignored.

## Out of scope

- A write path. Managers still submit docs as Markdown files; Mongo is a read model.
- Embeddings, rerankers, and chunking (above).
- Touching `server.py` — the protocol is there precisely so the MCP surface never learns which
  backend it is talking to.
