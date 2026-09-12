"""The MongoDB backend, exercised against mongomock so no server is required.

Behaviour that needs a real mongod — the $text index the external RAG system queries —
lives in test_mongo_integration.py, which skips itself when nothing is listening.
"""

import mongomock
import pytest

from agenda_kb import config
from agenda_kb.ingest import ingest
from agenda_kb.models import AgendaDoc, AgendaDocNotFound
from agenda_kb.mongo_store import MongoAgendaStore
from agenda_kb.store import AgendaStore, MarkdownAgendaStore

QUERIES = [
    "atlas",
    "product 1",
    "what is the checkout team aiming for?",
    "Marcus Bell",
    "infrastructure",
    "reliability streaming",
]


@pytest.fixture
def collection():
    return mongomock.MongoClient()[config.MONGO_DB][config.MONGO_COLLECTION]


@pytest.fixture
def mongo_store(store, collection):
    ingest(store, collection)
    return MongoAgendaStore(collection)


def test_mongo_store_satisfies_the_agenda_store_protocol(mongo_store):
    assert isinstance(mongo_store, AgendaStore)


def test_ingest_writes_one_document_per_agenda_doc(store, collection):
    report = ingest(store, collection)

    assert collection.count_documents({}) == len(store.list_docs()) == 2
    assert report.written == 2
    assert report.removed == []


def test_ingest_uses_the_doc_id_as_the_primary_key(store, collection):
    ingest(store, collection)

    document = collection.find_one({"_id": "atlas-checkout"})
    assert document is not None
    assert document["id"] == "atlas-checkout"
    assert document["title"] == "Atlas Checkout"
    assert document["aliases"] == ["product 1", "product #1", "checkout"]


def test_running_ingest_twice_does_not_duplicate_documents(store, collection):
    ingest(store, collection)
    second = ingest(store, collection)

    assert collection.count_documents({}) == 2
    assert second.written == 2


def test_ingest_removes_documents_whose_markdown_file_is_gone(kb_dir, store, collection):
    ingest(store, collection)

    (kb_dir / "platform-infrastructure.md").unlink()
    report = ingest(MarkdownAgendaStore(kb_dir), collection)

    assert report.removed == ["platform-infrastructure"]
    assert collection.count_documents({}) == 1
    assert collection.find_one({"_id": "platform-infrastructure"}) is None


def test_get_doc_resolves_an_id(mongo_store):
    assert mongo_store.get_doc("atlas-checkout").title == "Atlas Checkout"


def test_get_doc_resolves_an_alias(mongo_store):
    assert mongo_store.get_doc("product 1").id == "atlas-checkout"


def test_get_doc_resolves_a_name_with_odd_case_and_punctuation(mongo_store):
    assert mongo_store.get_doc("  Atlas-Checkout  ").id == "atlas-checkout"
    assert mongo_store.get_doc("product #1").id == "atlas-checkout"


def test_get_doc_raises_for_an_unknown_name(mongo_store):
    with pytest.raises(AgendaDocNotFound) as excinfo:
        mongo_store.get_doc("nonexistent-project")

    assert "nonexistent-project" in str(excinfo.value)


def test_the_body_round_trips_verbatim(store, mongo_store):
    for doc in store.list_docs():
        assert mongo_store.get_doc(doc.id).body == doc.body


def test_frontmatter_round_trips_field_for_field(store, mongo_store):
    for expected in store.list_docs():
        actual = mongo_store.get_doc(expected.id)
        for field in (*config.REQUIRED_FIELDS, "aliases", "tags", "summary"):
            assert getattr(actual, field) == getattr(expected, field), field


def test_list_docs_puts_current_agendas_first(store, mongo_store):
    assert [doc.id for doc in mongo_store.list_docs()] == [
        doc.id for doc in store.list_docs()
    ]


@pytest.mark.parametrize("query", QUERIES)
def test_search_ranks_identically_to_the_markdown_store(store, mongo_store, query):
    """The backend must not change the answer. Same query, same docs, same scores —
    otherwise swapping stores silently changes what the agent is told."""
    expected = [(result.doc.id, result.score) for result in store.search(query)]
    actual = [(result.doc.id, result.score) for result in mongo_store.search(query)]

    assert actual == expected


def test_search_respects_the_limit(mongo_store):
    assert len(mongo_store.search("agenda", limit=1)) <= 1


def test_a_malformed_document_is_skipped_rather_than_breaking_the_store(
    store, mongo_store, collection
):
    collection.insert_one({"_id": "broken-doc", "title": "No required fields"})

    assert [doc.id for doc in mongo_store.list_docs()] == [
        doc.id for doc in store.list_docs()
    ]
    with pytest.raises(AgendaDocNotFound):
        mongo_store.get_doc("broken-doc")


def test_documents_written_after_construction_are_visible_without_a_restart(
    store, mongo_store, collection
):
    """There is no in-memory cache to go stale: a doc ingested by someone else, into a
    database this store is already pointed at, is served on the next call."""
    before = len(mongo_store.list_docs())
    collection.insert_one(
        {
            "_id": "harbor-mobile",
            "id": "harbor-mobile",
            "title": "Harbor Mobile",
            "project": "Harbor",
            "department": "Product Engineering",
            "team": "Mobile Squad",
            "owner": "Priya Nandakumar",
            "status": "active",
            "period": "2026-H2",
            "updated": "2026-09-02",
            "aliases": ["mobile app"],
            "tags": ["mobile"],
            "summary": "The app merchants run their business from.",
            "body": "Harbor is the merchant app.",
            "lookup_keys": ["harbor mobile", "mobile app"],
        }
    )

    assert len(mongo_store.list_docs()) == before + 1
    assert mongo_store.get_doc("mobile app").id == "harbor-mobile"
