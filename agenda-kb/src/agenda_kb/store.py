"""Loading agenda docs out of storage and answering queries about them.

``AgendaStore`` is the contract the MCP surface depends on. ``MarkdownAgendaStore`` reads
the filesystem, which *is* the knowledge base — that is what makes submitting an agenda doc
as simple as adding a file. ``MongoAgendaStore`` in ``mongo_store.py`` reads the same docs
out of MongoDB for consumers that want a database rather than a directory. Which one runs is
``config.STORE_BACKEND``, resolved in ``create_store`` below and nowhere else; ``server.py``
cannot tell the difference.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional, Protocol, runtime_checkable

import yaml

from agenda_kb import config, search as search_module
from agenda_kb.models import AgendaDoc, AgendaDocNotFound, SearchResult

__all__ = [
    "AgendaDoc",
    "AgendaDocNotFound",
    "AgendaStore",
    "MarkdownAgendaStore",
    "SearchResult",
    "create_store",
]

logger = logging.getLogger(__name__)

_FRONTMATTER_DELIMITER = "---"


@runtime_checkable
class AgendaStore(Protocol):
    """Everything the MCP surface is allowed to know about the knowledge base."""

    def list_docs(self) -> list[AgendaDoc]:
        """Every doc, ordered for display."""

    def get_doc(self, identifier: str) -> AgendaDoc:
        """One doc by id or alias. Raises ``AgendaDocNotFound`` if there is no match."""

    def search(self, query: str, limit: int = ...) -> list[SearchResult]:
        """Docs relevant to ``query``, best first."""

    def reload(self) -> None:
        """Re-read from the underlying storage, picking up newly submitted docs."""


def parse_doc(text: str, path: Optional[Path] = None) -> AgendaDoc:
    """Parse one Markdown file with YAML frontmatter into an AgendaDoc.

    Raises ValueError with a message naming the file and the problem, so a manager can
    fix a rejected submission without reading the loader.
    """
    where = f" in {path}" if path else ""
    stripped = text.lstrip()
    if not stripped.startswith(_FRONTMATTER_DELIMITER):
        raise ValueError(f"missing YAML frontmatter block{where}")

    parts = stripped.split(_FRONTMATTER_DELIMITER, 2)
    if len(parts) < 3:
        raise ValueError(f"unterminated YAML frontmatter block{where}")

    _, raw_frontmatter, body = parts

    try:
        frontmatter = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter{where}: {exc}") from exc

    if not isinstance(frontmatter, dict):
        raise ValueError(f"frontmatter must be a mapping of fields{where}")

    missing = [field for field in config.REQUIRED_FIELDS if not frontmatter.get(field)]
    if missing:
        raise ValueError(f"missing required frontmatter {missing}{where}")

    body = body.strip()
    return AgendaDoc(
        id=str(frontmatter["id"]).strip(),
        title=str(frontmatter["title"]).strip(),
        project=str(frontmatter["project"]).strip(),
        department=str(frontmatter["department"]).strip(),
        team=str(frontmatter["team"]).strip(),
        owner=str(frontmatter["owner"]).strip(),
        status=str(frontmatter["status"]).strip().lower(),
        period=str(frontmatter["period"]).strip(),
        updated=str(frontmatter["updated"]).strip(),
        aliases=_as_string_list(frontmatter.get("aliases")),
        tags=_as_string_list(frontmatter.get("tags")),
        body=body,
        summary=summarize(body),
        path=path,
    )


def _as_string_list(value) -> list[str]:
    """Accept a YAML list, a single scalar, or nothing at all."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def summarize(body: str) -> str:
    """The first line of real prose — skipping headings, blockquotes, and callouts.

    A doc's opening paragraph is its mission statement, which is exactly the gist a
    caller needs to decide whether to read the whole thing.
    """
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ">", "-", "*", "|", "`")):
            continue
        if len(line) <= config.SUMMARY_MAX_CHARS:
            return line
        truncated = line[: config.SUMMARY_MAX_CHARS].rsplit(" ", 1)[0]
        return f"{truncated}…"
    return ""


class MarkdownAgendaStore:
    """Agenda docs as Markdown files in a directory. Loaded eagerly, cached in memory."""

    def __init__(self, kb_dir: Path | str = config.KB_DIR) -> None:
        self.kb_dir = Path(kb_dir)
        self._docs: dict[str, AgendaDoc] = {}
        self._index: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        docs: dict[str, AgendaDoc] = {}
        for path in sorted(self.kb_dir.glob(config.DOC_GLOB)):
            try:
                doc = parse_doc(path.read_text(encoding="utf-8"), path=path)
            except (ValueError, OSError) as exc:
                # One malformed submission must not take the knowledge base offline.
                logger.warning("Skipping agenda doc %s: %s", path.name, exc)
                continue
            if doc.id in docs:
                logger.warning(
                    "Duplicate agenda doc id %r in %s; keeping %s",
                    doc.id,
                    path.name,
                    docs[doc.id].path.name if docs[doc.id].path else "the earlier doc",
                )
                continue
            docs[doc.id] = doc

        self._docs = docs
        self._index = self._build_lookup_index(docs.values())

    @staticmethod
    def _build_lookup_index(docs: Iterable[AgendaDoc]) -> dict[str, str]:
        """Every name a doc answers to -> its id.

        Names are normalized through the same tokenizer retrieval uses, so
        'atlas-checkout', 'Atlas Checkout', 'product 1' and 'product #1' are one key
        shape. Ids are registered before aliases so a doc's own id always wins, and an
        alias two docs both claim is logged rather than silently resolved — two docs
        answering to one name is a content bug in the KB, not something to paper over.
        """
        index: dict[str, str] = {}
        for doc in docs:
            index[_normalize_key(doc.id)] = doc.id
        for doc in docs:
            for alias in doc.aliases:
                key = _normalize_key(alias)
                if not key or index.get(key) == doc.id:
                    continue
                if key in index:
                    logger.warning(
                        "Name %r is claimed by both %r and %r; resolving to %r",
                        alias,
                        index[key],
                        doc.id,
                        index[key],
                    )
                    continue
                index[key] = doc.id
        return index

    def list_docs(self) -> list[AgendaDoc]:
        """Current agendas first, then alphabetically — the order a person would want."""
        return sorted(
            self._docs.values(), key=lambda doc: (not doc.is_current, doc.department, doc.id)
        )

    def get_doc(self, identifier: str) -> AgendaDoc:
        doc_id = self._index.get(_normalize_key(identifier))
        if doc_id is None:
            raise AgendaDocNotFound(
                f"No agenda doc matches {identifier!r}. "
                f"Available docs: {', '.join(sorted(self._docs)) or '(none)'}"
            )
        return self._docs[doc_id]

    def search(self, query: str, limit: int = config.DEFAULT_SEARCH_LIMIT) -> list[SearchResult]:
        limit = max(1, min(int(limit), config.MAX_SEARCH_LIMIT))
        return search_module.search(self._docs.values(), query, limit)


def _normalize_key(identifier: str) -> str:
    """Collapse a name to its bare tokens, so punctuation, case, hyphens, and spacing
    stop being three different ways to fail to find the same doc."""
    return " ".join(search_module.tokenize(str(identifier)))


def create_store() -> AgendaStore:
    """The single wiring point. Swapping the KB backend happens here and nowhere else.

    ``AGENDA_STORE_BACKEND=mongo`` serves from MongoDB instead of the filesystem. The
    Markdown store stays the default because it needs no running service: a clone of this
    repo answers questions immediately, with the database as an opt-in.
    """
    if config.STORE_BACKEND == "mongo":
        from agenda_kb.mongo_store import MongoAgendaStore

        logger.info("Serving agenda docs from MongoDB at %s", config.MONGO_URI)
        return _wrap_retrieval(MongoAgendaStore.from_uri())

    if config.STORE_BACKEND not in ("markdown", ""):
        logger.warning(
            "Unknown AGENDA_STORE_BACKEND %r; falling back to the Markdown KB",
            config.STORE_BACKEND,
        )
    return _wrap_retrieval(MarkdownAgendaStore(config.KB_DIR))


def _wrap_retrieval(docs: AgendaStore) -> AgendaStore:
    """Apply ``config.RETRIEVAL_MODE``. Hybrid retrieval needs a vector collection and the
    embeddings extra, so a failure to build it degrades to lexical with a warning — a KB
    that answers slightly worse beats a server that will not start."""
    if config.RETRIEVAL_MODE != "hybrid":
        return docs

    try:
        from pymongo import MongoClient

        from agenda_kb.embeddings import create_embedder
        from agenda_kb.hybrid import HybridAgendaStore, HybridRetriever
        from agenda_kb.semantic import SemanticRetriever
        from agenda_kb.vector_store import MongoChunkStore

        client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=config.MONGO_TIMEOUT_MS)
        chunks = MongoChunkStore(client[config.MONGO_DB][config.VECTOR_COLLECTION])
        if not chunks.count():
            raise RuntimeError(
                f"{config.MONGO_DB}.{config.VECTOR_COLLECTION} is empty — "
                "run `agenda-kb-ingest --embed`"
            )
        semantic = SemanticRetriever(create_embedder(), chunks, docs)
        logger.info("Hybrid retrieval enabled (%s + lexical)", config.EMBEDDING_MODEL)
        return HybridAgendaStore(docs, HybridRetriever(docs, semantic))
    except Exception as exc:  # noqa: BLE001 - any failure here must not stop the server
        logger.warning("Hybrid retrieval unavailable (%s); using lexical only", exc)
        return docs
