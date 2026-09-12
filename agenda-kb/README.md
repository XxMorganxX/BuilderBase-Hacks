# agenda-kb

A queryable knowledge base of **agenda docs** — the documents in which each department or
product team states what it is aiming for in a period — exposed over MCP so an agent can
select the right one for whatever project it is currently discussing.

> **The sample data is fictional.** `kb/agenda/` contains invented agenda docs that borrow
> Apple's product vocabulary — AirPods, macOS, and a made-up "iPhone Duo". Owners, metrics,
> dates, and codenames are all fabricated, no real person is named, and none of it reflects
> any real company's plans. It exists to exercise retrieval against prose that reads like the
> real thing.

## What's here

A manager submits an agenda doc by dropping a Markdown file with YAML frontmatter into
`kb/agenda/` — the filesystem is the knowledge base. `kb/SCHEMA.md` is the contract.

Three tools over MCP: `list_agenda_docs` to browse, `find_agenda_doc` to select,
`get_agenda_doc` to read one in full.

## Quick start

Nothing to run but the server — the Markdown files are the default backend:

```bash
uv run agenda-kb
```

MongoDB as the store, for consumers that want a database rather than a directory:

```bash
docker compose up -d                                  # MongoDB on 127.0.0.1:27018
uv run agenda-kb-ingest                               # sync kb/agenda/*.md into Mongo
AGENDA_STORE_BACKEND=mongo uv run agenda-kb
```

Hybrid retrieval, adding bge-m3 vectors over per-section chunks:

```bash
uv sync --extra embeddings                            # torch + sentence-transformers
uv run --extra embeddings agenda-kb-ingest --embed
AGENDA_STORE_BACKEND=mongo AGENDA_RETRIEVAL=hybrid uv run agenda-kb
```

## Retrieval, measured

`tests/test_retrieval_eval.py` holds 38 paraphrased queries with the doc each should return.
Numbers over the 32 in-corpus cases:

| retriever | hit@1 | hit@3 |
|---|---|---|
| lexical (weighted token scoring) | 28/32 — 87.5% | 30/32 — 93.8% |
| bge-m3 dense vectors only | 27/32 — 84.4% | — |
| **hybrid, fused with RRF** | **29/32 — 90.6%** | **32/32 — 100%** |

bge-m3 alone is *worse* than lexical here: names and aliases are lookups, not searches, and a
person's name has no semantic neighbourhood. It earns its place on paraphrase — "which team
worries about overheating" finds the doc that says "thermal envelope" — which is why the two
are fused rather than one replacing the other.

Run `pytest` for the suite. The semantic half skips itself unless the vectors and the model
are both present, so nobody who skipped that setup pays the model load.

## Layout

```
kb/SCHEMA.md          the frontmatter contract managers write against
kb/agenda/*.md        the knowledge base — one file per agenda doc
src/agenda_kb/
  config.py           every tuneable value and every agent-facing string
  store.py            AgendaStore protocol + Markdown backend + the wiring point
  mongo_store.py      MongoDB backend (a read model; Markdown stays authoritative)
  search.py           lexical scoring and tokenization
  chunks.py           splitting a doc into the sections that get embedded
  embeddings.py       the Embedder seam; bge-m3 via sentence-transformers
  vector_store.py     chunk vectors in Mongo, $vectorSearch with a cosine fallback
  semantic.py         query -> vector -> nearest sections -> docs
  hybrid.py           RRF fusion of the two retrievers
  server.py           the MCP surface
tasks/                working logs: what was decided, what was measured, what failed
```

Swapping a backend is a config change, not a code change: `AGENDA_STORE_BACKEND`,
`AGENDA_RETRIEVAL`, `AGENDA_MONGO_URI`, `AGENDA_EMBEDDING_MODEL`.

The `tasks/` logs carry the reasoning — including the approaches that did not work, like the
first chunking strategy that made bge-m3 score 71.9%.
