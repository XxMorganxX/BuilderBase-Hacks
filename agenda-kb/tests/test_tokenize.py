"""Tokenization, including the stemmer every matching path depends on.

Stemming lives in ``tokenize()`` because scoring, alias lookup, phrase bonuses, and the
Mongo ``lookup_keys`` all funnel through it. These tests pin the cases where a naive
"strip the trailing s" rule would quietly break a real doc name.
"""

import pytest

from agenda_kb.search import tokenize


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Product #1", ["product", "1"]),
        ("PRODUCT-1", ["product", "1"]),
        ("  Atlas-Checkout  ", ["atlas", "checkout"]),
    ],
)
def test_punctuation_case_and_spacing_collapse(text, expected):
    assert tokenize(text) == expected


@pytest.mark.parametrize(
    "plural, singular",
    [
        ("earbuds", "earbud"),
        ("headphones", "headphone"),
        ("AirPods", "airpod"),
        ("objectives", "objective"),
        ("metrics", "metric"),
        ("displays", "display"),
        ("aliases", "alias"),
        ("dependencies", "dependency"),
        ("batteries", "battery"),
        ("boxes", "box"),
    ],
)
def test_a_plural_and_its_singular_produce_the_same_token(plural, singular):
    """The whole point: a query and a doc that disagree about number still match."""
    assert tokenize(plural) == tokenize(singular)


@pytest.mark.parametrize(
    "word",
    [
        "ios",       # would become "io"
        "macos",     # would become "maco"
        "mac",       # too short to touch
        "os",
        "status",    # -us
        "business",  # -ss
        "analysis",  # -is
        "duo",
        "airpod",
    ],
)
def test_words_the_stemmer_must_leave_alone(word):
    assert tokenize(word) == [word]


def test_stemming_is_idempotent():
    """Stemming a stem must not shorten it again, or query and document drift apart
    depending on which side was already singular."""
    for word in ("earbuds", "dependencies", "aliases", "boxes", "displays"):
        once = tokenize(word)
        assert tokenize(" ".join(once)) == once
