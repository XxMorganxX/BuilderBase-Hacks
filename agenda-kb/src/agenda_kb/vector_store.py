"""Chunk vectors in MongoDB, and the step that writes them.

Retrieval prefers Atlas ``$vectorSearch`` and falls back to computing cosine in Python
when no vector index exists. The fallback is not a consolation prize: it is what makes the
code runnable on a plain ``mongod``, testable under mongomock, and honest about a corpus
of a few dozen chunks, where scanning them all costs less than a second round trip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from pymongo.collection import Collection
from pymongo.errors import OperationFailure, PyMongoError

from agenda_kb import config
from agenda_kb.chunks import Chunk, split_doc
from agenda_kb.embeddings import Embedder, cosine
from agenda_kb.store import AgendaStore

__all__ = ["ChunkHit", "EmbedReport", "MongoChunkStore", "embed_chunks", "to_chunk_document"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkHit:
    """One matching section, with the doc it came from so the caller can read it in full."""

    chunk_id: str
    doc_id: str
    heading: str
    text: str
    score: float


@dataclass
class EmbedReport:
    docs: int = 0
    chunks: int = 0
    pruned_docs: list[str] = field(default_factory=list)
    model: str = ""
    vector_index: bool = False

    def describe(self, target: str) -> str:
        lines = [
            f"Embedded {self.chunks} section(s) from {self.docs} agenda doc(s) "
            f"into {target}",
            f"  model: {self.model}",
            f"  vector index: {'created/present' if self.vector_index else 'unavailable — cosine fallback'}",
        ]
        for doc_id in self.pruned_docs:
            lines.append(f"  - pruned chunks for {doc_id} (no longer in the KB)")
        return "\n".join(lines)


def to_chunk_document(chunk: Chunk, vector: Sequence[float], embedder: Embedder) -> dict[str, Any]:
    """A chunk as its MongoDB row. ``model`` and ``dimensions`` ride along on every row so
    a collection that ends up holding two models' vectors is detectable instead of
    quietly returning nonsense."""
    return {
        "_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "doc_title": chunk.doc_title,
        "ordinal": chunk.ordinal,
        "heading": chunk.heading,
        "text": chunk.text,
        "embed_text": chunk.embed_text,
        "embedding": list(vector),
        "model": embedder.model_name,
        "dimensions": len(vector),
    }


class MongoChunkStore:
    """The chunk collection: write, prune, and vector search."""

    def __init__(self, collection: Collection) -> None:
        self.collection = collection

    def count(self) -> int:
        return self.collection.count_documents({})

    def ensure_vector_index(self, dimensions: int) -> bool:
        """Create the Atlas vector index if the server has one. Returns whether it exists.

        Plain ``mongod`` and mongomock have no search indexes, and that is fine — the
        cosine fallback covers them. A missing index must never fail an ingest.
        """
        definition = {
            "name": config.VECTOR_INDEX,
            "type": "vectorSearch",
            "definition": {
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": dimensions,
                        "similarity": config.VECTOR_SIMILARITY,
                    }
                ]
            },
        }
        try:
            existing = {index["name"] for index in self.collection.list_search_indexes()}
            if config.VECTOR_INDEX in existing:
                return True
            self.collection.create_search_index(definition)
            logger.info("Created vector index %s", config.VECTOR_INDEX)
            return True
        except (
            PyMongoError,
            OperationFailure,
            NotImplementedError,
            AttributeError,
            # mongomock resolves any unknown attribute to a sub-collection, so a method it
            # does not implement surfaces as "not callable" rather than AttributeError.
            TypeError,
        ) as exc:
            logger.info(
                "No vector index (%s: %s) — semantic search will use the cosine fallback",
                type(exc).__name__,
                exc,
            )
            return False

    def replace_doc_chunks(self, doc_id: str, rows: Sequence[dict[str, Any]]) -> None:
        """Delete-then-insert for one doc, so an edit that produces fewer sections does not
        leave the extra ones behind as ghosts."""
        self.collection.delete_many({"doc_id": doc_id})
        if rows:
            self.collection.insert_many(list(rows))

    def prune(self, keep_doc_ids: Sequence[str]) -> list[str]:
        """Drop chunks whose doc is no longer in the knowledge base."""
        keep = set(keep_doc_ids)
        stale = sorted(
            {
                str(row["doc_id"])
                for row in self.collection.find({}, {"doc_id": 1})
                if str(row["doc_id"]) not in keep
            }
        )
        if stale:
            self.collection.delete_many({"doc_id": {"$in": stale}})
        return stale

    def search(
        self, vector: Sequence[float], limit: int, model: Optional[str] = None
    ) -> list[ChunkHit]:
        hits = self._vector_search(vector, limit, model)
        if hits is None:
            hits = self._cosine_search(vector, limit, model)
        return hits

    def _vector_search(
        self, vector: Sequence[float], limit: int, model: Optional[str]
    ) -> Optional[list[ChunkHit]]:
        """Atlas ``$vectorSearch``. Returns None — not an empty list — when the stage is
        unavailable, so the caller can tell "no index" from "no matches"."""
        pipeline = [
            {
                "$vectorSearch": {
                    "index": config.VECTOR_INDEX,
                    "path": "embedding",
                    "queryVector": list(vector),
                    "numCandidates": limit * config.VECTOR_CANDIDATE_FACTOR,
                    "limit": limit,
                }
            },
            {
                "$project": {
                    "doc_id": 1,
                    "heading": 1,
                    "text": 1,
                    "model": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        try:
            rows = list(self.collection.aggregate(pipeline))
        except (PyMongoError, OperationFailure, NotImplementedError, TypeError) as exc:
            logger.debug("$vectorSearch unavailable (%s)", type(exc).__name__)
            return None
        return [
            ChunkHit(
                chunk_id=str(row["_id"]),
                doc_id=str(row["doc_id"]),
                heading=str(row.get("heading", "")),
                text=str(row.get("text", "")),
                score=round(float(row["score"]), 4),
            )
            for row in rows
            if model is None or row.get("model") == model
        ]

    def _cosine_search(
        self, vector: Sequence[float], limit: int, model: Optional[str]
    ) -> list[ChunkHit]:
        """Score every comparable chunk in Python. Rows from another model, or of another
        dimensionality, are skipped rather than compared against the wrong geometry."""
        hits: list[ChunkHit] = []
        for row in self.collection.find({}):
            stored = row.get("embedding") or []
            if len(stored) != len(vector):
                continue
            if model is not None and row.get("model") != model:
                continue
            hits.append(
                ChunkHit(
                    chunk_id=str(row["_id"]),
                    doc_id=str(row["doc_id"]),
                    heading=str(row.get("heading", "")),
                    text=str(row.get("text", "")),
                    score=round(cosine(vector, stored), 4),
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return hits[:limit]


def embed_chunks(
    source: AgendaStore, chunk_store: MongoChunkStore, embedder: Embedder
) -> EmbedReport:
    """Split every doc into sections, embed them, and replace what is stored.

    Idempotent like the document ingest: re-running it converges rather than accumulating,
    and chunks belonging to a deleted doc are pruned.
    """
    report = EmbedReport(model=embedder.model_name)
    report.vector_index = chunk_store.ensure_vector_index(embedder.dimensions)

    doc_ids: list[str] = []
    for doc in source.list_docs():
        chunks = split_doc(doc)
        if not chunks:
            continue
        vectors = embedder.embed_documents([chunk.embed_text for chunk in chunks])
        rows = [
            to_chunk_document(chunk, vector, embedder)
            for chunk, vector in zip(chunks, vectors)
        ]
        chunk_store.replace_doc_chunks(doc.id, rows)
        report.docs += 1
        report.chunks += len(rows)
        doc_ids.append(doc.id)

    report.pruned_docs = chunk_store.prune(doc_ids)
    return report
