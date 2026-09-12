"""Chunking, vector storage, and semantic retrieval — driven by a fake embedder.

No model and no server here: a deterministic bag-of-tokens embedder exercises the
mechanics (chunk boundaries, upsert, pruning, ranking, the cosine fallback) in
milliseconds. Whether **bge-m3** actually recovers the queries lexical retrieval misses is
a different question, answered against the real model in test_semantic_eval.py.
"""

import hashlib
import math

import mongomock
import pytest

from agenda_kb import config
from agenda_kb.chunks import split_doc
from agenda_kb.embeddings import cosine
from agenda_kb.semantic import SemanticRetriever
from agenda_kb.vector_store import MongoChunkStore, embed_chunks


class FakeEmbedder:
    """Hashes tokens into a small vector. Similarity tracks token overlap, which is enough
    to assert that ranking works without asserting anything about a real model."""

    model_name = "fake-bag-of-tokens"
    dimensions = 32

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in text.lower().split():
            digest = hashlib.sha1(token.encode()).digest()
            vector[digest[0] % self.dimensions] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def chunk_store():
    collection = mongomock.MongoClient()[config.MONGO_DB][config.VECTOR_COLLECTION]
    return MongoChunkStore(collection)


@pytest.fixture
def retriever(store, chunk_store, embedder):
    embed_chunks(store, chunk_store, embedder)
    return SemanticRetriever(embedder, chunk_store, store)


# --- chunking ---------------------------------------------------------------------------


def test_a_doc_is_split_on_its_second_level_headings(store):
    doc = store.get_doc("atlas-checkout")
    chunks = split_doc(doc)

    assert chunks, "a doc with headings produced no chunks"
    assert all(chunk.doc_id == doc.id for chunk in chunks)
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))


def test_chunk_text_does_not_repeat_the_heading_line(store):
    for chunk in split_doc(store.get_doc("atlas-checkout")):
        assert not chunk.text.lstrip().startswith("##")


def test_the_embedded_text_carries_the_doc_title(store):
    """A section read in isolation has to still say which product it belongs to, or the
    vector for 'Explicitly not doing' is about nothing in particular."""
    doc = store.get_doc("atlas-checkout")
    for chunk in split_doc(doc):
        assert doc.title in chunk.embed_text


def test_a_doc_with_no_headings_still_produces_one_chunk(store):
    doc = store.get_doc("platform-infrastructure")
    assert len(split_doc(doc)) >= 1


# --- storage ----------------------------------------------------------------------------


def test_embedding_writes_one_row_per_chunk(store, chunk_store, embedder):
    report = embed_chunks(store, chunk_store, embedder)

    expected = sum(len(split_doc(doc)) for doc in store.list_docs())
    assert report.chunks == expected
    assert chunk_store.count() == expected


def test_every_stored_chunk_carries_a_vector_of_the_embedder_dimensions(retriever, chunk_store, embedder):
    for row in chunk_store.collection.find({}):
        assert len(row["embedding"]) == embedder.dimensions
        assert row["model"] == embedder.model_name


def test_re_embedding_replaces_chunks_instead_of_duplicating_them(store, chunk_store, embedder):
    embed_chunks(store, chunk_store, embedder)
    before = chunk_store.count()
    embed_chunks(store, chunk_store, embedder)

    assert chunk_store.count() == before


def test_chunks_of_a_deleted_doc_are_pruned(kb_dir, store, chunk_store, embedder):
    from agenda_kb.store import MarkdownAgendaStore

    embed_chunks(store, chunk_store, embedder)
    (kb_dir / "platform-infrastructure.md").unlink()

    report = embed_chunks(MarkdownAgendaStore(kb_dir), chunk_store, embedder)

    assert report.pruned_docs == ["platform-infrastructure"]
    assert chunk_store.collection.count_documents({"doc_id": "platform-infrastructure"}) == 0


# --- retrieval --------------------------------------------------------------------------


def test_vector_search_returns_the_closest_chunk_first(retriever, chunk_store, embedder):
    vector = embedder.embed_query("one-tap checkout for returning buyers")
    hits = chunk_store.search(vector, limit=3)

    assert hits
    assert hits[0].doc_id == "atlas-checkout"
    assert hits[0].score >= hits[-1].score


def test_search_falls_back_to_cosine_when_there_is_no_vector_index(retriever):
    """mongomock has no $vectorSearch. Retrieval must still work, or nothing is testable
    and nothing runs on a plain mongod either."""
    assert retriever.search_chunks("checkout conversion", limit=2)


def test_doc_level_search_returns_each_doc_once_scored_by_its_best_chunk(retriever):
    results = retriever.search("checkout", limit=5)

    ids = [result.doc.id for result in results]
    assert ids == list(dict.fromkeys(ids)), "the same doc came back more than once"
    assert results[0].doc.id == "atlas-checkout"


def test_doc_level_search_respects_the_limit(retriever):
    assert len(retriever.search("agenda", limit=1)) == 1


def test_a_chunk_stored_by_a_different_model_is_ignored(retriever, chunk_store):
    """Mixing vectors from two models in one collection silently ruins ranking. Rows from
    another model are skipped, not compared."""
    chunk_store.collection.insert_one(
        {
            "_id": "ghost#0",
            "doc_id": "ghost",
            "heading": "Ghost",
            "text": "checkout checkout checkout",
            "embed_text": "checkout",
            "embedding": [1.0] * 3,
            "model": "some-other-model",
            "dimensions": 3,
        }
    )

    assert all(hit.doc_id != "ghost" for hit in retriever.search_chunks("checkout", limit=10))


def test_cosine_is_a_similarity_not_a_distance():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# --- chunking long sections -------------------------------------------------------------

LONG_OBJECTIVES = """
# Example — H2 2026 Agenda

## Agenda for the period

1. **Ship the first thing.** A paragraph of justification that runs on for long enough to
   look like the real agenda docs, because the whole point is that this section is the
   longest one in the document and dominates any vector built from it.
2. **Ship the second thing.** Another paragraph, on a completely different subject from the
   first, with its own metrics and its own dependencies and its own reasons for existing.
3. **Ship the third thing.** A third subject again, unrelated to the other two, which is
   exactly why averaging all three into one vector destroys the signal for each of them.
   Real agenda objectives run to several sentences, carry their own numbers, and name the
   teams they depend on, which is what pushes this section past the split threshold the way
   the real documents do.

## Success metrics

- One short line.
""".strip()


def build_doc(body, doc_id="example", title="Example"):
    from agenda_kb.models import AgendaDoc

    return AgendaDoc(
        id=doc_id,
        title=title,
        project=title,
        department="Dept",
        team="Team",
        owner="Owner",
        status="active",
        period="2026-H2",
        updated="2026-09-12",
        body=body,
        summary="",
    )


def test_a_long_section_of_numbered_objectives_splits_into_one_chunk_per_objective():
    """Five objectives in one vector is the whole-doc centroid problem one level down."""
    chunks = split_doc(build_doc(LONG_OBJECTIVES))

    agenda_chunks = [c for c in chunks if c.heading == "Agenda for the period"]
    assert len(agenda_chunks) == 3
    assert "first thing" in agenda_chunks[0].text
    assert "second thing" in agenda_chunks[1].text
    assert "third thing" in agenda_chunks[2].text


def test_each_piece_of_a_split_section_keeps_its_heading_and_doc_title():
    for chunk in split_doc(build_doc(LONG_OBJECTIVES)):
        assert chunk.heading
        assert "Example" in chunk.embed_text


def test_ordinals_stay_sequential_when_a_section_splits():
    chunks = split_doc(build_doc(LONG_OBJECTIVES))
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_a_short_section_is_left_whole(store):
    """Splitting a 500-character bullet list into one-line chunks buys nothing and costs
    context, so only sections long enough to blur get split."""
    doc = store.get_doc("atlas-checkout")
    assert len(split_doc(doc)) == 1


# --- hybrid fusion ----------------------------------------------------------------------


@pytest.fixture
def hybrid(store, retriever):
    from agenda_kb.hybrid import HybridRetriever

    return HybridRetriever(store, retriever)


def test_a_doc_both_retrievers_rank_first_comes_first(hybrid):
    results = hybrid.search("checkout", limit=3)
    assert results[0].doc.id == "atlas-checkout"


def test_a_doc_only_the_lexical_side_finds_still_appears(store, retriever):
    """The point of fusion: neither retriever gets to veto the other's find."""
    from agenda_kb.hybrid import HybridRetriever

    lexical_only = HybridRetriever(store, retriever, semantic_weight=0.0)
    assert [r.doc.id for r in lexical_only.search("Marcus Bell", limit=3)][0] == (
        "platform-infrastructure"
    )


def test_zero_weighting_a_side_reproduces_the_other_sides_order(store, retriever):
    from agenda_kb.hybrid import HybridRetriever

    query = "infrastructure"
    lexical_only = HybridRetriever(store, retriever, semantic_weight=0.0)
    assert [r.doc.id for r in lexical_only.search(query, limit=5)] == [
        r.doc.id for r in store.search(query, limit=5)
    ]

    semantic_only = HybridRetriever(store, retriever, lexical_weight=0.0)
    assert [r.doc.id for r in semantic_only.search(query, limit=5)] == [
        r.doc.id for r in retriever.search(query, limit=5)
    ]


def test_fused_scores_descend(hybrid):
    scores = [result.score for result in hybrid.search("checkout payments", limit=5)]
    assert scores == sorted(scores, reverse=True)


def test_matched_fields_say_which_retriever_voted(hybrid):
    result = hybrid.search("checkout", limit=1)[0]
    assert any(field.startswith(("lexical #", "semantic #")) for field in result.matched_fields)


def test_an_empty_query_returns_nothing(hybrid):
    assert hybrid.search("   ", limit=5) == []


def test_the_hybrid_store_delegates_lookups_but_fuses_search(store, retriever, hybrid):
    """Getting a doc by id has an exact answer; only search is a ranking problem."""
    from agenda_kb.hybrid import HybridAgendaStore
    from agenda_kb.store import AgendaStore

    hybrid_store = HybridAgendaStore(store, hybrid)

    assert isinstance(hybrid_store, AgendaStore)
    assert hybrid_store.get_doc("product 1").id == "atlas-checkout"
    assert [d.id for d in hybrid_store.list_docs()] == [d.id for d in store.list_docs()]
    assert hybrid_store.search("checkout", limit=1)[0].doc.id == "atlas-checkout"
