import pytest

from agenda_kb.store import AgendaDocNotFound, MarkdownAgendaStore
from tests.conftest import write_doc


def test_loads_every_markdown_doc_in_the_directory(store):
    assert {doc.id for doc in store.list_docs()} == {
        "atlas-checkout",
        "platform-infrastructure",
    }


def test_parses_frontmatter_into_typed_fields(store):
    doc = store.get_doc("atlas-checkout")

    assert doc.title == "Atlas Checkout"
    assert doc.project == "Atlas"
    assert doc.department == "Payments"
    assert doc.owner == "Dana Okafor"
    assert doc.aliases == ["product 1", "product #1", "checkout"]


def test_body_excludes_the_frontmatter_block(store):
    body = store.get_doc("atlas-checkout").body

    assert "one-tap checkout" in body
    assert "Dana Okafor" not in body


def test_get_doc_resolves_an_alias_to_the_canonical_doc(store):
    assert store.get_doc("product 1").id == "atlas-checkout"


def test_get_doc_resolves_an_alias_written_with_punctuation(store):
    # People write "product #1" as often as "product 1"; both must land on one doc.
    assert store.get_doc("product #1").id == "atlas-checkout"


def test_get_doc_resolves_an_id_spelled_with_spaces_instead_of_hyphens(store):
    assert store.get_doc("Atlas Checkout").id == "atlas-checkout"


def test_get_doc_is_case_and_whitespace_insensitive(store):
    assert store.get_doc("  Atlas-Checkout  ").id == "atlas-checkout"


def test_get_doc_raises_for_an_unknown_id(store):
    with pytest.raises(AgendaDocNotFound) as excinfo:
        store.get_doc("nonexistent-doc")

    # The error names the available docs so a caller can recover without a second round trip.
    assert "atlas-checkout" in str(excinfo.value)


def test_summary_is_the_first_meaningful_line_of_the_body(store):
    assert store.get_doc("platform-infrastructure").summary.startswith("Stand up EU")


def test_docs_missing_required_frontmatter_are_skipped_not_fatal(kb_dir):
    write_doc(kb_dir, "broken", {"title": "No id field here"})

    store = MarkdownAgendaStore(kb_dir)

    assert "broken" not in {doc.id for doc in store.list_docs()}
    assert len(store.list_docs()) == 2


def test_reload_picks_up_a_newly_submitted_doc(kb_dir, store):
    assert len(store.list_docs()) == 2

    write_doc(
        kb_dir,
        "beacon-analytics",
        {
            "id": "beacon-analytics",
            "title": "Beacon Analytics",
            "project": "Beacon",
            "department": "Data",
            "team": "Analytics Platform",
            "owner": "Raj Mehta",
            "status": "active",
            "period": "2026-H2",
            "updated": "2026-08-29",
        },
    )
    store.reload()

    assert len(store.list_docs()) == 3
