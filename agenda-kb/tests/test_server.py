"""The MCP surface: what the agent actually reads, and that the KB on disk is valid."""

import asyncio

import pytest

from agenda_kb import config
from agenda_kb.models import SearchResult
from agenda_kb.server import format_catalogue, format_doc, format_results, mcp
from agenda_kb.store import MarkdownAgendaStore


@pytest.fixture
def docs(store):
    return store.list_docs()


def test_catalogue_lists_every_doc_with_the_id_needed_to_fetch_it(docs):
    output = format_catalogue(docs)

    assert "`atlas-checkout`" in output
    assert "`platform-infrastructure`" in output
    assert "Dana Okafor" in output


def test_catalogue_says_so_when_the_knowledge_base_is_empty():
    assert "empty" in format_catalogue([]).lower()


def test_results_lead_with_the_best_match_and_its_id(store):
    output = format_results(store.search("product 1"), "product 1")

    assert "1. **Atlas Checkout**" in output
    assert 'get_agenda_doc("atlas-checkout")' in output


def test_results_are_ordered_best_first(store):
    # Both docs match on period; only one is the checkout team.
    output = format_results(store.search("2026-H2 checkout"), "2026-H2 checkout")

    assert output.index("atlas-checkout") < output.index("platform-infrastructure")


def test_no_match_tells_the_agent_not_to_invent_an_agenda(store):
    output = format_results(store.search("badminton"), "badminton")

    assert "no agenda doc matches" in output.lower()
    assert "do not infer" in output.lower()


def test_formatted_doc_carries_the_full_body_not_just_the_summary(store):
    output = format_doc(store.get_doc("atlas-checkout"))

    assert "one-tap checkout" in output
    assert "Dana Okafor" in output


def test_formatted_doc_flags_an_agenda_that_is_no_longer_active(kb_dir, store):
    doc = store.get_doc("atlas-checkout")
    superseded = type(doc)(**{**doc.__dict__, "status": "archived"})

    output = format_doc(superseded)

    assert "archived" in output.lower()
    assert "superseded" in output.lower()


def test_the_three_tools_are_registered_and_read_only():
    tools = asyncio.run(mcp.list_tools())

    assert {tool.name for tool in tools} == {
        "list_agenda_docs",
        "find_agenda_doc",
        "get_agenda_doc",
    }
    assert all(tool.annotations.read_only_hint for tool in tools)


def test_every_tool_description_tells_the_agent_when_to_reach_for_it():
    tools = asyncio.run(mcp.list_tools())

    for tool in tools:
        assert tool.description and len(tool.description) > 80, tool.name


def test_a_doc_is_addressable_as_a_resource():
    templates = asyncio.run(mcp.list_resource_templates())

    assert [template.uri_template for template in templates] == ["agenda://{doc_id}"]


# --- The real knowledge base --------------------------------------------------------
# Deliberately free of hardcoded ids and counts: docs get added and renamed, and these
# should keep passing when they do. They assert the KB is *valid*, not what is in it.


def test_every_doc_on_disk_parses_successfully():
    store = MarkdownAgendaStore(config.KB_DIR)

    files_on_disk = list(config.KB_DIR.glob(config.DOC_GLOB))
    assert files_on_disk, f"no agenda docs found in {config.KB_DIR}"
    # A doc that fails to parse is skipped with a warning at load, so a count mismatch
    # is how a malformed submission surfaces.
    assert len(store.list_docs()) == len(files_on_disk)


def test_every_doc_is_reachable_by_its_own_id_and_aliases():
    store = MarkdownAgendaStore(config.KB_DIR)

    for doc in store.list_docs():
        assert store.get_doc(doc.id).id == doc.id
        for alias in doc.aliases:
            assert store.get_doc(alias).id == doc.id, f"{alias!r} does not reach {doc.id}"


def test_no_two_docs_claim_the_same_name():
    store = MarkdownAgendaStore(config.KB_DIR)

    claims = {}
    for doc in store.list_docs():
        for name in [doc.id, *doc.aliases]:
            key = " ".join(name.lower().split())
            assert key not in claims, f"{name!r} claimed by both {claims[key]} and {doc.id}"
            claims[key] = doc.id


def test_every_doc_has_a_summary_worth_showing():
    store = MarkdownAgendaStore(config.KB_DIR)

    for doc in store.list_docs():
        assert len(doc.summary) > 20, f"{doc.id} has no usable summary line"


# --- Ambiguity ----------------------------------------------------------------------
# A near-tie means the query genuinely did not identify one doc. Handing the agent a
# winner anyway is how it confidently answers with the wrong team's agenda.


def _result(doc, score):
    return SearchResult(doc=doc, score=score, matched_fields=["aliases"])


def test_a_clear_winner_points_the_agent_straight_at_it(store):
    results = [
        _result(store.get_doc("atlas-checkout"), 50.0),
        _result(store.get_doc("platform-infrastructure"), 5.0),
    ]

    output = format_results(results, "checkout")

    assert 'get_agenda_doc("atlas-checkout")' in output
    assert "ambiguous" not in output.lower()


def test_a_near_tie_tells_the_agent_to_disambiguate_instead_of_guessing(store):
    results = [
        _result(store.get_doc("atlas-checkout"), 28.0),
        _result(store.get_doc("platform-infrastructure"), 27.0),
    ]

    output = format_results(results, "ios")

    assert "ambiguous" in output.lower()
    assert "atlas-checkout" in output and "platform-infrastructure" in output
