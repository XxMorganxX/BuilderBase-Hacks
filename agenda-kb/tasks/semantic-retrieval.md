# Task: Make retrieval good enough to measure — stemming, aliases, an eval set, and bge-m3

## Goal

Retrieval quality, in the order the evidence justified:

1. **Stemming.** `search.py` matches tokens by exact equality, so `"earbud"` misses the alias
   `earbuds` and the doc survives on a body match alone — scored 1 where `"earbuds"` scored 22.
2. **Alias holes.** `"headphones"` returns *nothing* from either backend. No doc contains the
   word; they say earbuds, AirPods, audio.
3. **An eval set.** Twenty-odd paraphrased queries with the doc each should return, run as a test,
   so retrieval changes are measured instead of argued.
4. **Semantic retrieval with bge-m3.** For the queries no alias list can cover — "which team
   worries about overheating" shares no token with the Duo agenda's "thermal envelope".

Items 1–3 are cheap and deterministic and come first on purpose: they fix most of what the probe
found without adding a model to the loop, and item 3 is what tells us whether item 4 earned it.

## Approach

Stemming goes inside `search.tokenize()` rather than beside it. Every name-matching path — scoring,
alias lookup, phrase bonuses, the Mongo `lookup_keys` — already funnels through that one function,
so stemming there keeps query-side and document-side normalization identical by construction. Two
functions that both have to remember to stem is the bug this avoids.

Embeddings live behind an `Embedder` protocol. bge-m3 runs in-process through
`sentence-transformers`, as an **optional** dependency group: the MCP server must not require torch
to answer a question. Chunking is per `##` section into its own collection — a doc covering five
objectives has a muddy centroid, and "Explicitly not doing" is exactly the section a whole-doc
vector loses.

## Decisions

| Decision | Rationale |
|---|---|
| Fix stemming before adding embeddings | A scoring bug that makes `"earbud"` score 1 instead of 22 is not a semantic-retrieval problem, and no encoder would have hidden it. |
| A conservative suffix stemmer, not Porter and not a dependency | Both sides of the comparison run through the same function, so consistency is what matters, not linguistic correctness. Rules skip short words and `-ss/-us/-is/-os` endings, because `ios` must not become `io` and `macos` must not become `maco`. |
| Stem inside `tokenize()` | It is the single entry point every matching path already uses. Anywhere else means two places that must agree forever. |
| bge-m3 via `sentence-transformers`, in-process | No Ollama on this machine, and installing one would add a background daemon to register and keep alive. In-process means no service, and the embedder is swappable anyway. |
| bge-m3 dense vectors only | Ollama and sentence-transformers both expose the dense head. bge-m3's sparse and ColBERT heads need `FlagEmbedding`; dense at 1024 dims is the simple path and the corpus is tiny. |
| `embeddings` as an optional dependency group | torch is ~2GB of install for a KB that answers fine without it. `uv sync` stays light; `uv sync --extra embeddings` opts in. |
| Section-level chunks in their own collection | The granularity a RAG answer cites. Whole-doc vectors blur five objectives into one centroid — my own argument against them last turn. |
| Atlas-local image for the container | `$vectorSearch` does not exist in plain `mongo:7`. The image is already cached on this machine, and it is the same query surface as production Atlas. A Python cosine fallback keeps the code testable without a vector index. |
| The eval set lives in a test, not a notebook | A quality number that nobody runs is a number that stops being true. As a test it gates regressions. |

## Progress log

### 2026-09-12 — decision recorded
User chose **bge-m3** after the probe. Kicked off `sentence-transformers` as an optional extra.
Ollama is not installed; the Atlas-local image is cached (1.95GB) so local `$vectorSearch` needs
no pull.

### 2026-09-12 — stemming
`_singularize` added inside `search.tokenize()`, with the skip-list that keeps `ios`, `macos`,
`status`, and `business` intact. `"earbud latency"` went from **1** to **28**. All 65 pre-existing
tests stayed green, which is the real evidence that the normalization is consistent on both sides.

### 2026-09-12 — alias and tag holes
`headphones` and `airpods audio` onto the AirPods doc; `fold`, `folding phone`, `foldable` onto the
Duo doc; `macbook`, `mac platform`, `desktop software` onto macOS. Topical tags added where the doc
covers the topic but never names it as an alias — `handoff`, `lossless`, `multipoint`, `hinge`,
`posture`, `thermal`, `tiling`, `rosetta`, `permissions`. `"headphones"` went from **nothing** to a
clean hit.

### 2026-09-12 — the eval set
38 cases in `tests/test_retrieval_eval.py`: 32 in-corpus, 6 that no doc covers. Labels came from
running them, not from taste — four are marked `lexical=False` because the token scorer provably
gets them wrong, and a test asserts those labels are still accurate so the set cannot quietly
become dishonest.

**Lexical baseline: hit@1 28/32 = 87.5%, hit@3 30/32 = 93.8%.**

An unplanned finding: the strongest out-of-corpus score (1.0) and the weakest true-positive score
(1.7) are 0.7 apart. Lexical scores cannot reliably separate "no answer" from "weak answer" on this
corpus, so the out-of-corpus test asserts only that nothing reaches a *confident* 5.0.

### 2026-09-12 — bge-m3, first attempt: worse than lexical
16 chunks, one per `##` section. **hit@1 23/32 = 71.9%** — worse than lexical. It
recovered 1 of the 4 lexical misses and broke 6 cases lexical had right.

Diagnosis from the `matched_fields`: every semantic hit came back via the same chunk, "Agenda for
the period", which runs ~1600 characters against ~600 for every other section. Five unrelated
objectives in one vector produced a chunk that was the nearest neighbour to almost everything. The
whole-doc centroid problem, one level down, exactly where I had claimed chunking would matter.

### 2026-09-12 — chunking fix
Sections longer than 700 characters now split on their top-level list items: 16 chunks became 31,
sizes 139–745 instead of 299–1686. Re-embedded.

**bge-m3 hit@1 27/32 = 84.4%, and all four lexical misses recovered** — including "which team
worries about overheating" → the Duo agenda, which shares no token with it.

Still 5 regressions against lexical, and they are all the same kind: `Ines Okonkwo`, `Gideon Park`,
`Nadia Farrokhzad`, `the fold`, `app store apps that were never recompiled`. Names and exact
identifiers. A person's name has no semantic neighbourhood — it is a lookup, and dense retrieval
answers lookups with vibes.

### 2026-09-12 — hybrid, and the number that mattered
`HybridRetriever` fuses the two rankings with Reciprocal Rank Fusion. Fusion uses ranks, not
scores, because weighted token counts in the tens and cosine values in [0, 1] cannot be added.

| retriever | hit@1 | hit@3 |
|---|---|---|
| lexical only | 28/32 — 87.5% | 30/32 — 93.8% |
| bge-m3 only | 27/32 — 84.4% | — |
| **hybrid (RRF)** | **29/32 — 90.6%** | **32/32 — 100%** |

hit@3 is the number that matters in practice: `find_agenda_doc` hands the agent several matches, so
the right doc being second is recoverable. It is now never absent.

A grid over `k` ∈ {5, 10, 20, 60} and five weight pairs showed `k` is irrelevant at three docs, and
a 1.0/0.7 tilt toward lexical scoring 30/32. **Not taken.** That is one query on a 32-query set, and
tuning weights on the only eval set in existence is how you ship a number that does not survive the
next twenty questions. Equal weights, `k=60` from the RRF paper.

The two remaining failures are both cases where lexical is confidently *wrong*: `"dropouts on a
crowded subway platform"` — "platform" is macOS Platform's own name — and `"overheating"`. Rank
fusion cannot outvote a wrong rank-1 when the other retriever only has it at rank 2.

### 2026-09-12 — wiring and one real bug
`AGENDA_RETRIEVAL=hybrid` builds the fused store in `create_store()`; `HybridAgendaStore` fuses
`search` and passes `list_docs`/`get_doc` straight through, because those have exact answers and are
not ranking problems. A failure to build the hybrid path degrades to lexical with a warning rather
than refusing to start.

Loading the model emitted ~40 INFO lines, one per HuggingFace cache check. This server speaks
JSON-RPC over stdio, so stderr is the only channel a human reads; named third-party loggers are now
raised to WARNING on model load. Verified: 0 HTTP log lines.

**128 tests pass.**

## How to use it

```bash
docker compose up -d                                    # MongoDB on 127.0.0.1:27018
uv sync --extra embeddings                              # torch + sentence-transformers
uv run --extra embeddings agenda-kb-ingest --embed      # docs + bge-m3 section vectors
AGENDA_STORE_BACKEND=mongo AGENDA_RETRIEVAL=hybrid uv run agenda-kb
```

`pytest` runs the eval set automatically. The semantic half skips itself unless the vectors and the
model are both present, so nobody who skipped the embedding setup pays the ~8s model load.

For a RAG system reading Mongo directly, the chunk contract is `agenda_kb.agenda_chunks`:
`_id` (`<doc_id>#<ordinal>`), `doc_id`, `doc_title`, `heading`, `text` (what to quote), `embed_text`
(what was embedded — title + heading + text), `embedding` (1024 floats, normalized), `model`, and
`dimensions`. Rows carry their model so a collection holding two models' vectors is detectable
rather than silently mis-ranked.

## Open questions

- **The first query pays ~8 seconds** of model load. Fine for a CLI, visible in a server. Warming
  the embedder at startup would hide it; a small quantized model or an HTTP embedding service would
  remove it.
- **No vector index.** `mongo:7` has no `$vectorSearch`, so retrieval uses the exact cosine
  fallback. At 31 chunks that is *better* — exact instead of approximate, and no index to maintain.
  The `mongodb/mongodb-atlas-local` image is cached on this machine when the corpus outgrows it; the
  code already prefers `$vectorSearch` whenever the index exists.
- **Dense retrieval cannot say "no".** Out-of-corpus questions score 0.375–0.494, inside the
  0.270–0.698 range of real hits. A similarity cutoff would cost real answers. "No doc matches" has
  to keep coming from the lexical side or from the agent. A cross-encoder reranker is the standard
  fix and would probably also settle the two remaining hybrid failures.
- **bge-m3's sparse and ColBERT heads are unused.** sentence-transformers exposes only the dense
  head; `FlagEmbedding` gives all three, and bge-m3's own hybrid scoring might beat RRF over two
  separate retrievers. Untested.
- **32 queries is a small eval set.** Every number above has a confidence interval wide enough to
  swallow a single-query difference, which is exactly why the weight tilt was declined.

## Out of scope

- A reranker.
- Tuning RRF weights further without a larger eval set.
- Swapping the container to Atlas-local for `$vectorSearch` at 31 chunks.
