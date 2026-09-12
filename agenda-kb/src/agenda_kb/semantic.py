"""Semantic retrieval: query -> vector -> nearest sections -> the docs they belong to.

Deliberately a *separate* retriever rather than a mode of the lexical one. They disagree
about the same query often enough that hiding which answered would make retrieval
impossible to debug, and their scores are on different scales — cosine similarity in
[0, 1] against weighted token counts in the tens.
"""

from __future__ import annotations

import logging

from agenda_kb import config
from agenda_kb.embeddings import Embedder
from agenda_kb.models import AgendaDocNotFound, SearchResult
from agenda_kb.store import AgendaStore
from agenda_kb.vector_store import ChunkHit, MongoChunkStore

__all__ = ["SemanticRetriever"]

logger = logging.getLogger(__name__)


class SemanticRetriever:
    """Finds docs by the meaning of their sections rather than by the words in them."""

    def __init__(
        self,
        embedder: Embedder,
        chunk_store: MongoChunkStore,
        doc_store: AgendaStore,
    ) -> None:
        self.embedder = embedder
        self.chunk_store = chunk_store
        self.doc_store = doc_store

    def search_chunks(
        self, query: str, limit: int = config.DEFAULT_SEMANTIC_LIMIT
    ) -> list[ChunkHit]:
        """The passages themselves — what a RAG answer should quote and cite."""
        if not query.strip():
            return []
        vector = self.embedder.embed_query(query)
        return self.chunk_store.search(vector, limit, model=self.embedder.model_name)

    def search(
        self, query: str, limit: int = config.DEFAULT_SEMANTIC_LIMIT
    ) -> list[SearchResult]:
        """Doc-level results, so this is directly comparable with the lexical store.

        A doc is scored by its single best section, not by the average of its sections: a
        doc with one perfect section and four irrelevant ones is a good answer, and
        averaging would bury it under a doc that is vaguely on-topic throughout.
        """
        hits = self.search_chunks(query, limit * config.SEMANTIC_DOC_FANOUT)

        best: dict[str, ChunkHit] = {}
        for hit in hits:
            if hit.doc_id not in best or hit.score > best[hit.doc_id].score:
                best[hit.doc_id] = hit

        results: list[SearchResult] = []
        for doc_id, hit in sorted(best.items(), key=lambda item: -item[1].score):
            try:
                doc = self.doc_store.get_doc(doc_id)
            except AgendaDocNotFound:
                # A chunk whose doc has gone: stale index, not a reason to fail a query.
                logger.warning("Chunk %s references unknown doc %s", hit.chunk_id, doc_id)
                continue
            results.append(
                SearchResult(doc=doc, score=hit.score, matched_fields=[f"section: {hit.heading}"])
            )
        return results[:limit]
