"""Turning text into vectors.

``Embedder`` is the seam. The model is the component most likely to change here — a
different encoder, a hosted API, a quantized local build — so nothing outside this module
names bge-m3 or imports sentence-transformers, and swapping it is a change to
``create_embedder`` plus a re-run of the embed step.

The import of ``sentence_transformers`` is deliberately deferred into the model property:
importing torch costs seconds, and a KB served from the Markdown store should never pay
that just because this module is importable.
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Protocol, Sequence

from agenda_kb import config

__all__ = ["Embedder", "SentenceTransformerEmbedder", "cosine", "create_embedder"]

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """What the rest of the package is allowed to know about an embedding model."""

    @property
    def model_name(self) -> str:
        """Recorded on every vector, so rows from two models are never compared."""

    @property
    def dimensions(self) -> int:
        """Length of the vectors this embedder produces."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages, in the order given."""

    def embed_query(self, text: str) -> list[float]:
        """Embed one search query. Separate from documents because some models want an
        instruction prefix on the query side — bge-m3 does not, but the seam stays."""


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity, and 0.0 for a zero vector rather than a ZeroDivisionError."""
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


class SentenceTransformerEmbedder:
    """bge-m3 (or any sentence-transformers model) loaded in-process.

    In-process rather than behind an HTTP server because that is one fewer thing to run,
    start, and register. The cost is a slow first call while the model loads.
    """

    def __init__(
        self,
        model_name: str = config.EMBEDDING_MODEL,
        dimensions: int = config.EMBEDDING_DIMENSIONS,
        device: Optional[str] = config.EMBEDDING_DEVICE,
        batch_size: int = config.EMBEDDING_BATCH_SIZE,
        normalize: bool = config.EMBEDDING_NORMALIZE,
        query_prefix: str = config.EMBEDDING_QUERY_PREFIX,
    ) -> None:
        self._model_name = model_name
        self._dimensions = dimensions
        self._device = device
        self._batch_size = batch_size
        self._normalize = normalize
        self._query_prefix = query_prefix
        self._model = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model(self):
        """Loaded on first use. The first call pays for it; later calls do not."""
        if self._model is None:
            _quiet_dependency_logs()
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model %s", self._model_name)
            self._model = SentenceTransformer(self._model_name, device=self._device)
            # Renamed in sentence-transformers 6; support both without a version check.
            measure = getattr(
                self._model, "get_embedding_dimension", None
            ) or self._model.get_sentence_embedding_dimension
            actual = measure()
            if actual != self._dimensions:
                # Mismatched dimensions mean every stored vector is unusable. Say so now,
                # not after writing a collection nobody can query.
                raise ValueError(
                    f"{self._model_name} produces {actual}-dim vectors but config says "
                    f"{self._dimensions}; fix config.EMBEDDING_DIMENSIONS"
                )
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.model.encode(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=self._normalize,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([f"{self._query_prefix}{text}"])[0]


def _quiet_dependency_logs() -> None:
    """Stop the model stack from flooding stderr on first load.

    Scoped to named third-party loggers and never touches the root logger or this package's
    own, so an application that wants those logs can still set them itself afterwards.
    """
    if not config.EMBEDDING_QUIET_DEPENDENCY_LOGS:
        return
    for name in config.EMBEDDING_NOISY_LOGGERS:
        noisy = logging.getLogger(name)
        if noisy.level < logging.WARNING:
            noisy.setLevel(logging.WARNING)


def create_embedder() -> Embedder:
    """The single wiring point for the embedding model."""
    return SentenceTransformerEmbedder()
