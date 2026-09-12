"""The retrieval eval set: paraphrased questions, and the doc each one should return.

This runs against the **real** KB in ``kb/agenda/``, not a fixture, because the number it
produces is only meaningful about the actual corpus. It skips if the KB is empty.

Labels came from measurement, not from taste. Every case marked ``lexical=False`` is one
the weighted token scorer provably gets wrong today — those are the cases semantic
retrieval has to earn its keep on, and ``test_semantic_retrieval.py`` asserts it does.

Run `pytest tests/test_retrieval_eval.py -s` to see the per-query table.
"""

from dataclasses import dataclass

import pytest

from agenda_kb import config
from agenda_kb.store import MarkdownAgendaStore

#: Hit@1 over in-corpus cases must not fall below this. Measured at 0.875 with stemming
#: and the current alias lists; the floor sits just under that so a real regression fails
#: while noise does not.
LEXICAL_HIT_AT_1_FLOOR = 0.85

#: Hit@3 — the agent sees the top few matches, so a doc in third place is still findable.
LEXICAL_HIT_AT_3_FLOOR = 0.93

#: A score this high means at least one weighted frontmatter field matched, not just a
#: stray body token. Out-of-corpus questions must never reach it.
CONFIDENT_MATCH_SCORE = 5.0


@dataclass(frozen=True)
class EvalCase:
    query: str
    expected: str | None  # the doc id that should come back; None if no doc covers it
    lexical: bool = True  # False: known lexical failure, kept as the case for embeddings
    note: str = ""


EVAL_SET = [
    # --- AirPods Link Protocol ------------------------------------------------------
    EvalCase("what is the airpods team aiming for?", "airpods-link-protocol"),
    EvalCase("who owns the wireless audio protocol", "airpods-link-protocol"),
    EvalCase("headphones", "airpods-link-protocol", note="synonym, via aliases"),
    EvalCase("earbud latency", "airpods-link-protocol", note="singular vs plural alias"),
    EvalCase("lossless audio over bluetooth", "airpods-link-protocol"),
    EvalCase("LE Audio certification", "airpods-link-protocol"),
    EvalCase("multipoint handoff between devices", "airpods-link-protocol"),
    EvalCase("Ines Okonkwo", "airpods-link-protocol", note="by owner"),
    EvalCase("who owns the radio link", "airpods-link-protocol"),
    EvalCase("how long do the earbuds last on a charge", "airpods-link-protocol"),
    EvalCase(
        "dropouts on a crowded subway platform",
        "airpods-link-protocol",
        lexical=False,
        note="'platform' is macOS Platform's name; it hijacks the query",
    ),
    EvalCase(
        "noise cancellation",
        "airpods-link-protocol",
        lexical=False,
        note="term absent from the KB; this is still the team to route to",
    ),
    # --- iPhone Duo iOS -------------------------------------------------------------
    EvalCase("what is the iPhone Duo team working on", "iphone-duo-ios"),
    EvalCase("the fold", "iphone-duo-ios"),
    EvalCase("foldable app compatibility", "iphone-duo-ios"),
    EvalCase("posture API", "iphone-duo-ios"),
    EvalCase("hinge sensor calibration", "iphone-duo-ios"),
    EvalCase("app store apps that were never recompiled", "iphone-duo-ios"),
    EvalCase("Nadia Farrokhzad", "iphone-duo-ios", note="by owner"),
    EvalCase("a phone that bends in half", "iphone-duo-ios"),
    EvalCase("battery life when unfolded", "iphone-duo-ios"),
    EvalCase("two apps side by side on a bigger screen", "iphone-duo-ios"),
    EvalCase(
        "which team worries about overheating",
        "iphone-duo-ios",
        lexical=False,
        note="doc says 'thermal envelope'; no token in common",
    ),
    # --- macOS Platform -------------------------------------------------------------
    EvalCase("macOS agenda", "macos-platform"),
    EvalCase("window tiling adoption", "macos-platform"),
    EvalCase("Rosetta deprecation", "macos-platform"),
    EvalCase("desktop operating system", "macos-platform"),
    EvalCase("too many permission popups on a new mac", "macos-platform"),
    EvalCase("Gideon Park", "macos-platform", note="by owner"),
    EvalCase("laptop software", "macos-platform"),
    EvalCase("people clicking allow without reading", "macos-platform"),
    EvalCase(
        "intel apps migration",
        "macos-platform",
        lexical=False,
        note="loses 4-to-3 to the Duo doc, which says 'apps' far more often",
    ),
    # --- Nothing in the KB covers these ---------------------------------------------
    EvalCase("accessibility roadmap", None),
    EvalCase("hiring plan for new graduates", None),
    EvalCase("quarterly revenue forecast", None),
    EvalCase("office relocation", None),
    EvalCase("retail store staffing", None),
    EvalCase("supply chain logistics", None),
]

IN_CORPUS = [case for case in EVAL_SET if case.expected]
OUT_OF_CORPUS = [case for case in EVAL_SET if case.expected is None]
LEXICAL_EXPECTED = [case for case in IN_CORPUS if case.lexical]
SEMANTIC_ONLY = [case for case in IN_CORPUS if not case.lexical]


@pytest.fixture(scope="module")
def kb_store():
    store = MarkdownAgendaStore(config.KB_DIR)
    if not store.list_docs():
        pytest.skip(f"No agenda docs in {config.KB_DIR}")
    return store


def rank_of_expected(store, case) -> int | None:
    """1-based position of the expected doc, or None if it is not in the results."""
    for position, result in enumerate(store.search(case.query, limit=config.MAX_SEARCH_LIMIT), 1):
        if result.doc.id == case.expected:
            return position
    return None


def test_lexical_retrieval_gets_every_case_it_is_expected_to(kb_store):
    """The regression gate. These 28 queries work today; if one stops working, something
    in tokenization, weighting, or the docs' own aliases broke."""
    failures = [
        (case.query, case.expected, rank_of_expected(kb_store, case))
        for case in LEXICAL_EXPECTED
        if rank_of_expected(kb_store, case) != 1
    ]
    assert not failures, f"{len(failures)} case(s) regressed: {failures}"


def test_lexical_hit_at_1_stays_above_the_floor(kb_store):
    hits = sum(1 for case in IN_CORPUS if rank_of_expected(kb_store, case) == 1)
    rate = hits / len(IN_CORPUS)
    print(f"\nlexical hit@1: {hits}/{len(IN_CORPUS)} = {rate:.1%}")
    assert rate >= LEXICAL_HIT_AT_1_FLOOR


def test_lexical_hit_at_3_stays_above_the_floor(kb_store):
    hits = sum(
        1
        for case in IN_CORPUS
        if (rank := rank_of_expected(kb_store, case)) is not None and rank <= 3
    )
    rate = hits / len(IN_CORPUS)
    print(f"lexical hit@3: {hits}/{len(IN_CORPUS)} = {rate:.1%}")
    assert rate >= LEXICAL_HIT_AT_3_FLOOR


@pytest.mark.parametrize("case", OUT_OF_CORPUS, ids=lambda case: case.query)
def test_no_doc_is_confidently_returned_for_a_question_the_kb_does_not_cover(kb_store, case):
    """Inventing an agenda is worse than admitting there isn't one."""
    results = kb_store.search(case.query)
    top = results[0].score if results else 0.0
    assert top < CONFIDENT_MATCH_SCORE, (
        f"{case.query!r} confidently returned "
        f"{results[0].doc.id} at {top} — the KB has no doc for it"
    )


def test_the_known_lexical_failures_are_still_failing(kb_store):
    """Guards the eval set's own honesty: if a case labelled ``lexical=False`` starts
    passing lexically, the label is stale and the semantic case for it has weakened."""
    still_failing = [
        case.query for case in SEMANTIC_ONLY if rank_of_expected(kb_store, case) != 1
    ]
    assert still_failing == [case.query for case in SEMANTIC_ONLY], (
        "a lexical=False case now passes lexically; re-label it"
    )
