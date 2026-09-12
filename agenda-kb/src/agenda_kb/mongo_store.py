"""Agenda docs stored in MongoDB.

A second implementation of the ``AgendaStore`` protocol, next to ``MarkdownAgendaStore``.
``server.py`` cannot tell which one it is talking to, which was the point of the protocol.

Two things are deliberate here:

*Mongo is a read model.* The Markdown files in ``kb/agenda/`` remain the source of truth —
a manager still submits a doc by adding a file — and ``ingest.py`` projects them into this
collection. So this class never writes. Dropping the database loses nothing that one
``agenda-kb-ingest`` cannot rebuild.

*Ranking does not live here.* Search loads documents and hands them to ``search.py``, the
same scorer the Markdown store uses, so swapping backends cannot quietly change what the
agent is told. Mongo's own ``$text`` index is still created by ingest, for the RAG systems
that query this collection directly — see ``config.MONGO_TEXT_WEIGHTS``.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from agenda_kb import config, search as search_module
from agenda_kb.models import AgendaDoc, AgendaDocNotFound, SearchResult

__all__ = ["MongoAgendaStore", "document_to_doc", "lookup_keys_for", "to_document"]

logger = logging.getLogger(__name__)

#: The body is the expensive field and listings never show it, so catalogue reads leave
#: it behind. Anything the scorer or a catalogue entry touches has to be in here.
_CATALOGUE_PROJECTION = {"body": 0}


def normalize_key(value: str) -> str:
    """Collapse a name to bare tokens — the same normalization the Markdown store's
    lookup index uses, so both backends answer to exactly the same set of names."""
    return " ".join(search_module.tokenize(str(value)))


def lookup_keys_for(doc: AgendaDoc) -> list[str]:
    """Every normalized name this doc answers to, id first.

    Precomputing these at ingest time is what makes ``get_doc`` a single indexed query
    instead of a scan with normalization applied in Python.
    """
    keys = [normalize_key(doc.id)]
    for name in (doc.title, *doc.aliases):
        key = normalize_key(name)
        if key and key not in keys:
            keys.append(key)
    return keys


def to_document(doc: AgendaDoc) -> dict[str, Any]:
    """An AgendaDoc as the MongoDB document a RAG system will read.

    ``_id`` is the doc's own id: it is already unique and kebab-case, so it buys primary-key
    lookups and upsert-shaped ingest for free. ``id`` repeats it because MongoDB refuses to
    put ``_id`` in a text index, and because a document exported to JSON should say what it
    is without the reader knowing Mongo conventions.
    """
    return {
        "_id": doc.id,
        "id": doc.id,
        "title": doc.title,
        "project": doc.project,
        "department": doc.department,
        "team": doc.team,
        "owner": doc.owner,
        "status": doc.status,
        "period": doc.period,
        "updated": doc.updated,
        "aliases": list(doc.aliases),
        "tags": list(doc.tags),
        "summary": doc.summary,
        "body": doc.body,
        "lookup_keys": lookup_keys_for(doc),
        "source_path": str(doc.path) if doc.path else None,
    }


def document_to_doc(document: dict[str, Any]) -> AgendaDoc:
    """Rebuild an AgendaDoc from a stored document.

    Raises ValueError naming the offending field, so a hand-edited document is reported
    the same way a malformed Markdown file is.
    """
    missing = [field for field in config.REQUIRED_FIELDS if not document.get(field)]
    if missing:
        raise ValueError(f"document {document.get('_id')!r} missing fields {missing}")

    return AgendaDoc(
        id=str(document["id"]),
        title=str(document["title"]),
        project=str(document["project"]),
        department=str(document["department"]),
        team=str(document["team"]),
        owner=str(document["owner"]),
        status=str(document["status"]).lower(),
        period=str(document["period"]),
        updated=str(document["updated"]),
        aliases=[str(alias) for alias in document.get("aliases") or []],
        tags=[str(tag) for tag in document.get("tags") or []],
        body=str(document.get("body") or ""),
        summary=str(document.get("summary") or ""),
        path=None,
    )


class MongoAgendaStore:
    """Agenda docs read from a MongoDB collection.

    Holds no cache. Every call reads the collection, so a doc ingested by another process
    is served on the next question with no restart and nothing to invalidate — which is
    also why ``reload()`` has nothing to do.
    """

    def __init__(self, collection: Collection) -> None:
        self.collection = collection

    @classmethod
    def from_uri(
        cls,
        uri: str = config.MONGO_URI,
        db_name: str = config.MONGO_DB,
        collection_name: str = config.MONGO_COLLECTION,
        timeout_ms: int = config.MONGO_TIMEOUT_MS,
    ) -> "MongoAgendaStore":
        """Connect using config defaults. The only place a client is constructed."""
        from pymongo import MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        return cls(client[db_name][collection_name])

    def reload(self) -> None:
        """Nothing to do: this store never caches. Present because the protocol has it,
        and cheap on purpose — ``server.py`` calls it before every query."""

    def list_docs(self) -> list[AgendaDoc]:
        """Current agendas first, then by department and id — the Markdown store's order."""
        docs = self._load(projection=_CATALOGUE_PROJECTION)
        return sorted(docs, key=lambda doc: (not doc.is_current, doc.department, doc.id))

    def get_doc(self, identifier: str) -> AgendaDoc:
        """One doc by id, title, or alias, in a single indexed query."""
        key = normalize_key(identifier)
        document = self.collection.find_one({"lookup_keys": key}) if key else None
        if document is None:
            raise AgendaDocNotFound(
                f"No agenda doc matches {identifier!r}. "
                f"Available docs: {', '.join(self._known_ids()) or '(none)'}"
            )
        return document_to_doc(document)

    def search(self, query: str, limit: int = config.DEFAULT_SEARCH_LIMIT) -> list[SearchResult]:
        """Rank docs through the shared scorer, so results match the Markdown store's.

        Scoring reads the body, so this cannot use the catalogue projection. For a KB of
        tens-to-hundreds of agenda docs one full read is cheaper than the round trips a
        two-phase candidate query would cost; ``$text`` or vector search is where this goes
        if the corpus ever outgrows that.
        """
        limit = max(1, min(int(limit), config.MAX_SEARCH_LIMIT))
        return search_module.search(self._load(), query, limit)

    def _load(self, projection: Optional[dict[str, int]] = None) -> list[AgendaDoc]:
        """Every well-formed doc in the collection. Malformed documents are skipped with a
        warning: one bad row must not take the knowledge base offline."""
        docs: list[AgendaDoc] = []
        for document in self._find(projection):
            try:
                docs.append(document_to_doc(document))
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping MongoDB agenda document: %s", exc)
        return docs

    def _find(self, projection: Optional[dict[str, int]] = None) -> Iterable[dict[str, Any]]:
        try:
            return list(self.collection.find({}, projection))
        except PyMongoError as exc:
            # An unreachable database is an empty KB, not a traceback in the agent's face.
            logger.error("MongoDB unavailable (%s: %s)", type(exc).__name__, exc)
            return []

    def _known_ids(self) -> list[str]:
        try:
            return sorted(str(doc["_id"]) for doc in self.collection.find({}, {"_id": 1}))
        except PyMongoError:
            return []
