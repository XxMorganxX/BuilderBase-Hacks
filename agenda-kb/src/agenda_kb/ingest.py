"""Project the Markdown knowledge base into MongoDB.

Run it after adding, editing, or deleting an agenda doc:

    docker compose up -d          # once, if the local database is not running
    uv run agenda-kb-ingest

Idempotent by construction. Every doc is upserted under its own id, and documents whose
Markdown file no longer exists are deleted — so this is a *sync*, not an append. A doc
removed from ``kb/agenda/`` disappears from the database on the next run rather than
lingering as a stale answer, which is the failure mode that matters in a RAG corpus.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field

from pymongo import ASCENDING, TEXT
from pymongo.collection import Collection
from pymongo.errors import OperationFailure, PyMongoError

from agenda_kb import config
from agenda_kb.mongo_store import to_document
from agenda_kb.store import AgendaStore, MarkdownAgendaStore

__all__ = ["IngestReport", "ensure_indexes", "ingest", "main"]

logger = logging.getLogger(__name__)


@dataclass
class IngestReport:
    """What one sync actually did — printed, and asserted on in tests."""

    written: int = 0
    removed: list[str] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)

    def describe(self, target: str) -> str:
        lines = [f"Synced {self.written} agenda doc(s) into {target}"]
        for doc_id in self.doc_ids:
            lines.append(f"  + {doc_id}")
        for doc_id in self.removed:
            lines.append(f"  - {doc_id} (removed: no longer in the Markdown KB)")
        return "\n".join(lines)


def ensure_indexes(collection: Collection) -> list[str]:
    """Create the indexes the store and the RAG consumers rely on.

    Two indexes, for two different readers:

    * ``lookup_keys`` — multikey, so ``get_doc("iphone duo")`` is an indexed lookup.
    * a weighted ``$text`` index — unused by this package, because ranking belongs to
      ``search.py``. It is here so a RAG system can query this collection natively with
      ``{"$text": {"$search": ...}}`` and get sensible weighting without reimplementing it.

    Index names are pinned in config so repeated runs converge instead of accumulating.
    """
    created = []
    try:
        created.append(
            collection.create_index(
                [("lookup_keys", ASCENDING)], name=config.MONGO_LOOKUP_INDEX
            )
        )
    except PyMongoError as exc:
        logger.warning("Could not create the lookup index: %s", exc)

    try:
        created.append(
            collection.create_index(
                [(field_name, TEXT) for field_name in config.MONGO_TEXT_WEIGHTS],
                weights=dict(config.MONGO_TEXT_WEIGHTS),
                name=config.MONGO_TEXT_INDEX,
            )
        )
    except (PyMongoError, OperationFailure, NotImplementedError) as exc:
        # mongomock does not implement text indexes, and an older server may refuse the
        # weights. Neither is fatal: nothing in this package reads that index.
        logger.info("Text index not created (%s): %s", type(exc).__name__, exc)

    return created


def ingest(source: AgendaStore, collection: Collection) -> IngestReport:
    """Sync every doc in ``source`` into ``collection`` and drop what is no longer there."""
    ensure_indexes(collection)

    report = IngestReport()
    for doc in source.list_docs():
        document = to_document(doc)
        collection.replace_one({"_id": doc.id}, document, upsert=True)
        report.written += 1
        report.doc_ids.append(doc.id)

    stale = [
        str(document["_id"])
        for document in collection.find({}, {"_id": 1})
        if str(document["_id"]) not in set(report.doc_ids)
    ]
    if stale:
        collection.delete_many({"_id": {"$in": stale}})
        report.removed = sorted(stale)

    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``agenda-kb-ingest``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uri", default=config.MONGO_URI, help="MongoDB connection string")
    parser.add_argument("--db", default=config.MONGO_DB, help="database name")
    parser.add_argument(
        "--collection", default=config.MONGO_COLLECTION, help="collection name"
    )
    parser.add_argument(
        "--kb-dir", default=str(config.KB_DIR), help="directory of Markdown agenda docs"
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help=(
            "also split each doc into sections and write bge-m3 vectors to the "
            f"{config.VECTOR_COLLECTION} collection (needs the 'embeddings' extra)"
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from pymongo import MongoClient

    client = MongoClient(args.uri, serverSelectionTimeoutMS=config.MONGO_TIMEOUT_MS)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        print(
            f"Cannot reach MongoDB at {args.uri}: {exc}\n"
            "Start the local instance with `docker compose up -d`, or pass --uri.",
            file=sys.stderr,
        )
        return 1

    source = MarkdownAgendaStore(args.kb_dir)
    if not source.list_docs():
        print(f"No agenda docs found in {args.kb_dir}; nothing to sync.", file=sys.stderr)
        return 1

    database = client[args.db]
    report = ingest(source, database[args.collection])
    print(report.describe(f"{args.db}.{args.collection} at {args.uri}"))

    if args.embed:
        # Imported here, not at module scope: embedding is an optional extra and the
        # document sync must not require torch to be installed.
        from agenda_kb.embeddings import create_embedder
        from agenda_kb.vector_store import MongoChunkStore, embed_chunks

        chunk_store = MongoChunkStore(database[config.VECTOR_COLLECTION])
        embed_report = embed_chunks(source, chunk_store, create_embedder())
        print(embed_report.describe(f"{args.db}.{config.VECTOR_COLLECTION}"))

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
