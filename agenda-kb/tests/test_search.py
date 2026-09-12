from tests.conftest import write_doc
from agenda_kb.store import MarkdownAgendaStore


def test_finds_a_doc_by_its_project_name(store):
    results = store.search("Atlas")

    assert results[0].doc.id == "atlas-checkout"


def test_finds_a_doc_by_an_alias_the_agent_would_actually_say(store):
    results = store.search("what is product 1 aiming for?")

    assert results[0].doc.id == "atlas-checkout"


def test_finds_a_doc_by_department(store):
    results = store.search("infrastructure")

    assert results[0].doc.id == "platform-infrastructure"


def test_finds_a_doc_by_its_owning_manager(store):
    results = store.search("Marcus Bell")

    assert results[0].doc.id == "platform-infrastructure"


def test_body_matches_rank_below_frontmatter_matches(kb_dir):
    # "checkout" is this doc's whole body but only an alias of atlas-checkout.
    write_doc(
        kb_dir,
        "mentions-checkout",
        {
            "id": "mentions-checkout",
            "title": "Unrelated Team",
            "project": "Unrelated",
            "department": "Other",
            "team": "Other Squad",
            "owner": "Someone Else",
            "status": "active",
            "period": "2026-H2",
            "updated": "2026-08-01",
        },
        body="We depend on checkout, checkout, checkout for everything we do.",
    )
    store = MarkdownAgendaStore(kb_dir)

    results = store.search("checkout")

    assert results[0].doc.id == "atlas-checkout"


def test_returns_nothing_when_no_doc_is_relevant(store):
    assert store.search("quarterly badminton tournament") == []


def test_respects_the_result_limit(store):
    assert len(store.search("2026-H2 active", limit=1)) == 1


def test_result_explains_which_fields_matched(store):
    result = store.search("Atlas")[0]

    assert "project" in result.matched_fields


def test_archived_docs_rank_below_active_ones(kb_dir):
    write_doc(
        kb_dir,
        "atlas-legacy",
        {
            "id": "atlas-legacy",
            "title": "Atlas Legacy",
            "project": "Atlas",
            "department": "Payments",
            "team": "Checkout Squad",
            "owner": "Dana Okafor",
            "status": "archived",
            "period": "2026-H1",
            "updated": "2026-01-10",
        },
        body="Old Atlas agenda.",
    )
    store = MarkdownAgendaStore(kb_dir)

    results = store.search("Atlas Payments Checkout Squad")

    assert results[0].doc.id == "atlas-checkout"
