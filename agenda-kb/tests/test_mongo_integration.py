"""End-to-end against a real MongoDB.

Skipped when nothing is listening on the configured URI, so `pytest` stays green on a
machine with no database. Start one with `docker compose up -d`.

These cover the two things mongomock cannot: that the ``$text`` index is actually created
and usable — it exists for RAG systems querying the collection directly — and that ranking
still matches the Markdown store when a real server is doing the reads.
"""

import pytest

pymongo = pytest.importorskip("pymongo")

from pymongo.errors import PyMongoError  # noqa: E402

from agenda_kb import config  # noqa: E402
from agenda_kb.ingest import ingest  # noqa: E402
from agenda_kb.mongo_store import MongoAgendaStore  # noqa: E402

#: A throwaway database, so a test run can never disturb the real KB.
TEST_DB = f"{config.MONGO_DB}_pytest"


@pytest.fixture(scope="module")
def client():
    client = pymongo.MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=1_500)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        pytest.skip(f"No MongoDB at {config.MONGO_URI} ({exc.__class__.__name__})")
    yield client
    client.drop_database(TEST_DB)
    client.close()


@pytest.fixture
def collection(client):
    client.drop_database(TEST_DB)
    return client[TEST_DB][config.MONGO_COLLECTION]


def test_ingest_creates_both_indexes(store, collection):
    ingest(store, collection)

    names = set(collection.index_information())
    assert config.MONGO_LOOKUP_INDEX in names
    assert config.MONGO_TEXT_INDEX in names


def test_the_text_index_is_queryable_by_an_outside_consumer(store, collection):
    """A RAG system that never imports this package still gets weighted retrieval."""
    ingest(store, collection)

    hits = list(
        collection.find(
            {"$text": {"$search": "checkout"}},
            {"score": {"$meta": "textScore"}, "title": 1},
        ).sort([("score", {"$meta": "textScore"})])
    )

    assert hits, "text index returned nothing for a term the KB clearly contains"
    assert hits[0]["_id"] == "atlas-checkout"


def test_alias_lookup_uses_the_index(store, collection):
    ingest(store, collection)
    mongo_store = MongoAgendaStore(collection)

    plan = collection.find({"lookup_keys": "product 1"}).explain()
    assert "IXSCAN" in str(plan), "alias lookup fell back to a collection scan"
    assert mongo_store.get_doc("product 1").id == "atlas-checkout"


def test_a_real_server_round_trips_the_whole_doc(store, collection):
    ingest(store, collection)
    mongo_store = MongoAgendaStore(collection)

    for expected in store.list_docs():
        actual = mongo_store.get_doc(expected.id)
        assert actual.body == expected.body
        assert actual.aliases == expected.aliases
        assert actual.summary == expected.summary


@pytest.mark.parametrize("query", ["atlas", "product 1", "infrastructure", "Dana Okafor"])
def test_ranking_matches_the_markdown_store_on_a_real_server(store, collection, query):
    ingest(store, collection)
    mongo_store = MongoAgendaStore(collection)

    expected = [(result.doc.id, result.score) for result in store.search(query)]
    actual = [(result.doc.id, result.score) for result in mongo_store.search(query)]

    assert actual == expected
