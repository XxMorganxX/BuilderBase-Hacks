"""The eval set, run against the real bge-m3 vectors.

Auto-skips unless the setup actually exists — sentence-transformers installed, MongoDB
reachable, and the chunk collection populated by `agenda-kb-ingest --embed` with the
configured model. Anyone who has done that setup gets the measurement for free; anyone who
has not pays nothing, including the ~8s of model load.

These are the numbers the embedding decision rests on, so they assert relationships
("hybrid beats both") rather than only absolute floors — a floor can be met by a retriever
that has quietly stopped contributing anything.
"""

import pytest

from agenda_kb import config
from agenda_kb.store import MarkdownAgendaStore
from test_retrieval_eval import IN_CORPUS, OUT_OF_CORPUS, SEMANTIC_ONLY

#: bge-m3 alone, over in-corpus queries. Measured 27/32 = 84.4% with per-section chunks.
SEMANTIC_HIT_AT_1_FLOOR = 0.78

#: Fusion. Measured 29/32 = 90.6% with equal weights.
HYBRID_HIT_AT_1_FLOOR = 0.87

pytestmark = pytest.mark.filterwarnings("ignore::FutureWarning")


@pytest.fixture(scope="module")
def retrievers():
    pytest.importorskip("sentence_transformers")
    pymongo = pytest.importorskip("pymongo")
    from pymongo.errors import PyMongoError

    from agenda_kb.embeddings import create_embedder
    from agenda_kb.hybrid import HybridRetriever
    from agenda_kb.semantic import SemanticRetriever
    from agenda_kb.vector_store import MongoChunkStore

    client = pymongo.MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=1_500)
    try:
        client.admin.command("ping")
    except PyMongoError:
        pytest.skip(f"No MongoDB at {config.MONGO_URI}")

    chunks = MongoChunkStore(client[config.MONGO_DB][config.VECTOR_COLLECTION])
    if not chunks.collection.count_documents({"model": config.EMBEDDING_MODEL}):
        pytest.skip(
            f"No {config.EMBEDDING_MODEL} vectors in "
            f"{config.MONGO_DB}.{config.VECTOR_COLLECTION} — run `agenda-kb-ingest --embed`"
        )

    docs = MarkdownAgendaStore(config.KB_DIR)
    if not docs.list_docs():
        pytest.skip("No agenda docs on disk")

    semantic = SemanticRetriever(create_embedder(), chunks, docs)
    yield docs, semantic, HybridRetriever(docs, semantic)
    client.close()


def hit_at(retriever, cases, k=1):
    hits = 0
    for case in cases:
        results = retriever.search(case.query, limit=config.MAX_SEARCH_LIMIT)
        if any(result.doc.id == case.expected for result in results[:k]):
            hits += 1
    return hits


def test_semantic_retrieval_recovers_every_case_lexical_gets_wrong(retrievers):
    """The reason bge-m3 is here at all. If this fails, the embeddings are not paying for
    themselves and the eval set's ``lexical=False`` labels should be revisited."""
    _, semantic, _ = retrievers

    failures = []
    for case in SEMANTIC_ONLY:
        results = semantic.search(case.query, limit=3)
        if not results or results[0].doc.id != case.expected:
            failures.append((case.query, results[0].doc.id if results else None, case.expected))

    assert not failures, f"semantic retrieval missed: {failures}"


def test_semantic_hit_at_1_meets_the_floor(retrievers):
    _, semantic, _ = retrievers
    hits = hit_at(semantic, IN_CORPUS)
    print(f"\nsemantic  hit@1: {hits}/{len(IN_CORPUS)} = {hits / len(IN_CORPUS):.1%}")
    assert hits / len(IN_CORPUS) >= SEMANTIC_HIT_AT_1_FLOOR


def test_hybrid_beats_both_retrievers_on_its_own(retrievers):
    """The load-bearing claim. Fusion has to be better than either input, or it is just
    added machinery and the right answer is to pick one."""
    lexical, semantic, hybrid = retrievers

    lexical_hits = hit_at(lexical, IN_CORPUS)
    semantic_hits = hit_at(semantic, IN_CORPUS)
    hybrid_hits = hit_at(hybrid, IN_CORPUS)
    n = len(IN_CORPUS)
    print(
        f"\nhit@1  lexical {lexical_hits}/{n}  semantic {semantic_hits}/{n}  "
        f"hybrid {hybrid_hits}/{n}"
    )

    assert hybrid_hits > lexical_hits
    assert hybrid_hits > semantic_hits
    assert hybrid_hits / n >= HYBRID_HIT_AT_1_FLOOR


def test_hybrid_always_puts_the_right_doc_in_the_top_three(retrievers):
    """What the agent actually sees. find_agenda_doc returns several matches, so the doc
    being second is recoverable; the doc being absent is not."""
    _, _, hybrid = retrievers
    hits = hit_at(hybrid, IN_CORPUS, k=3)
    print(f"hybrid    hit@3: {hits}/{len(IN_CORPUS)} = {hits / len(IN_CORPUS):.1%}")
    assert hits == len(IN_CORPUS)


def test_semantic_scores_do_not_separate_covered_from_uncovered_questions(retrievers):
    """A measured weakness, pinned so nobody assumes a cosine threshold can be trusted.

    Dense retrieval always returns its nearest neighbour, and on this corpus the scores for
    questions the KB does not cover sit inside the range of scores for questions it does.
    That is why "no doc matches" has to come from the lexical side or from the agent, not
    from a similarity cutoff. If this ever fails, a threshold has become viable — good news,
    and worth acting on.
    """
    _, semantic, _ = retrievers

    covered = [
        results[0].score
        for case in IN_CORPUS
        if (results := semantic.search(case.query, limit=1))
    ]
    uncovered = [
        results[0].score
        for case in OUT_OF_CORPUS
        if (results := semantic.search(case.query, limit=1))
    ]

    print(
        f"semantic top-1 score range — covered: {min(covered):.3f}-{max(covered):.3f}, "
        f"uncovered: {min(uncovered):.3f}-{max(uncovered):.3f}"
    )
    assert uncovered, "dense retrieval returned nothing at all for an uncovered question"
    assert max(uncovered) > min(covered), "the ranges no longer overlap — revisit thresholding"
