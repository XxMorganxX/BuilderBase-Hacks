"""Hybrid retrieval: fuse the lexical ranking with the semantic one.

Measured on the eval set, neither retriever alone is good enough, and they fail in
*different* places:

* Lexical wins identity. "Gideon Park", "the fold", "product 1" are lookups, not searches —
  a name either matches a doc's frontmatter or it does not, and embeddings add nothing but
  noise to that.
* Semantic wins paraphrase. "which team worries about overheating" has no token in common
  with the doc that says "thermal envelope", and no alias list will ever anticipate it.

Fusion is Reciprocal Rank Fusion: each retriever votes with ``weight / (k + rank)``. It
deliberately ignores both retrievers' scores, because weighted token counts in the tens and
cosine similarities in [0, 1] cannot be added or compared — only their orderings can.
"""

from __future__ import annotations

from typing import Optional, Protocol

from agenda_kb import config
from agenda_kb.models import AgendaDoc, SearchResult

__all__ = ["HybridAgendaStore", "HybridRetriever"]


class Retriever(Protocol):
    """The one method fusion needs from either side."""

    def search(self, query: str, limit: int = ...) -> list[SearchResult]:
        ...


class HybridRetriever:
    """Combines two retrievers by rank. Either one may be missing a doc entirely."""

    def __init__(
        self,
        lexical: Retriever,
        semantic: Retriever,
        k: int = config.RRF_K,
        lexical_weight: float = config.RRF_LEXICAL_WEIGHT,
        semantic_weight: float = config.RRF_SEMANTIC_WEIGHT,
    ) -> None:
        self.lexical = lexical
        self.semantic = semantic
        self.k = k
        self.lexical_weight = lexical_weight
        self.semantic_weight = semantic_weight

    def search(
        self, query: str, limit: int = config.DEFAULT_SEARCH_LIMIT, depth: Optional[int] = None
    ) -> list[SearchResult]:
        """Fused results, best first.

        ``depth`` is how deep to read each retriever before fusing; it defaults to enough
        that a doc buried by one retriever can still be rescued by the other.

        The returned ``score`` is a fusion score. It is **not** comparable to a lexical
        score or a cosine similarity, and it is not a probability — only its ordering is
        meaningful.
        """
        if not query.strip():
            return []

        depth = depth or max(limit * config.SEMANTIC_DOC_FANOUT, config.DEFAULT_SEARCH_LIMIT)

        contributions: dict[str, float] = {}
        best_result: dict[str, SearchResult] = {}
        matched: dict[str, list[str]] = {}

        for retriever, weight, label in (
            (self.lexical, self.lexical_weight, "lexical"),
            (self.semantic, self.semantic_weight, "semantic"),
        ):
            if not weight:
                continue
            for rank, result in enumerate(retriever.search(query, limit=depth), 1):
                doc_id = result.doc.id
                contributions[doc_id] = contributions.get(doc_id, 0.0) + weight / (self.k + rank)
                matched.setdefault(doc_id, []).append(f"{label} #{rank}")
                # Keep whichever retriever ranked it higher as the representative result,
                # so `matched_fields` explains the strongest evidence, not the last seen.
                if doc_id not in best_result or rank == 1:
                    best_result.setdefault(doc_id, result)

        ordered = sorted(contributions.items(), key=lambda item: (-item[1], item[0]))
        return [
            SearchResult(
                doc=best_result[doc_id].doc,
                score=round(score, 6),
                matched_fields=matched[doc_id] + best_result[doc_id].matched_fields,
            )
            for doc_id, score in ordered[:limit]
        ]


class HybridAgendaStore:
    """An ``AgendaStore`` whose ``search`` is fused, and whose other methods are not.

    Listing and getting a doc by id are not retrieval problems — they have exact answers —
    so they pass straight through to the underlying store. Only ``search`` is fused. This
    is what lets ``server.py`` get hybrid retrieval without knowing that embeddings, a
    vector collection, or a second retriever exist.
    """

    def __init__(self, docs, retriever: HybridRetriever) -> None:
        self.docs = docs
        self.retriever = retriever

    def list_docs(self) -> list[AgendaDoc]:
        return self.docs.list_docs()

    def get_doc(self, identifier: str) -> AgendaDoc:
        return self.docs.get_doc(identifier)

    def reload(self) -> None:
        self.docs.reload()

    def search(
        self, query: str, limit: int = config.DEFAULT_SEARCH_LIMIT
    ) -> list[SearchResult]:
        limit = max(1, min(int(limit), config.MAX_SEARCH_LIMIT))
        return self.retriever.search(query, limit=limit)
