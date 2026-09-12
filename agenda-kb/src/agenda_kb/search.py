"""Lexical retrieval over agenda docs.

Deliberately not embeddings. A knowledge base of agenda docs is small, and the thing a
caller is searching for is almost always a name the doc already declares — a project, a
team, an alias. Weighted token matching handles that exactly, stays debuggable (every
result can explain itself), and needs no index to rebuild when a manager edits a file.
"""

from __future__ import annotations

import re
from typing import Iterable

from agenda_kb import config
from agenda_kb.models import AgendaDoc, SearchResult

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

#: Below this length a trailing "s" is far more likely to be part of the word than a
#: plural marker. "ios" and "os" are the cases that matter here.
_MIN_STEM_LENGTH = 4

#: Endings where a trailing "s" is not a plural. Without these, "macos" becomes "maco",
#: "status" becomes "statu", and "business" becomes "busines" — each of which stops
#: matching the very docs it came from.
_NOT_PLURAL_ENDINGS = ("ss", "us", "is", "os", "as")

#: Letters before "-es" that mean the "e" belongs to the suffix, not the stem:
#: "aliases" -> "alias", "boxes" -> "box", while "devices" -> "device".
_ES_SUFFIX_STEMS = "sxzhc"


def _singularize(token: str) -> str:
    """Fold a plural onto its singular, conservatively.

    This is not linguistics. Query tokens and document tokens both pass through here, so
    the only property that matters is that the two sides agree — and that a real doc name
    is never mangled into something no document contains.
    """
    if len(token) < _MIN_STEM_LENGTH or not token.endswith("s"):
        return token
    if token.endswith(_NOT_PLURAL_ENDINGS):
        return token
    if token.endswith("ies"):
        return f"{token[:-3]}y"
    if token.endswith("es") and token[-3] in _ES_SUFFIX_STEMS:
        return token[:-2]
    return token[:-1]


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric runs, singularized.

    Punctuation, case, and '#' all disappear, so 'Product #1', 'product 1', and
    'PRODUCT-1' collapse to the same tokens. Plurals collapse onto singulars too, which is
    why a question about an "earbud" finds a doc that only ever says "earbuds".

    Every path that matches a name against a doc goes through this function — scoring,
    alias resolution, phrase bonuses, and the Mongo lookup keys — so normalization cannot
    drift between query side and document side.
    """
    return [_singularize(token) for token in _TOKEN_PATTERN.findall(text.lower())]


def _content_tokens(text: str) -> set[str]:
    """Tokens that carry meaning about *which* doc is wanted."""
    return {token for token in tokenize(text) if token not in config.STOPWORDS}


def _phrase_haystack(query: str) -> str:
    """The query as space-delimited tokens, padded, so a substring test on a padded
    phrase matches only on whole-word boundaries."""
    return f" {' '.join(tokenize(query))} "


def score_doc(doc: AgendaDoc, query: str) -> SearchResult | None:
    """Score one doc against a query. Returns None when nothing matched."""
    query_tokens = _content_tokens(query)
    if not query_tokens:
        return None

    score = 0.0
    matched_fields: list[str] = []

    for field_name, text in doc.searchable_fields().items():
        weight = config.FIELD_WEIGHTS.get(field_name)
        if not weight or not text:
            continue
        # Distinct tokens only: a doc that says "checkout" twelve times is not twelve
        # times more about checkout than one that says it once.
        hits = query_tokens & _content_tokens(text)
        if hits:
            score += weight * len(hits)
            matched_fields.append(field_name)

    if score == 0.0:
        return None

    haystack = _phrase_haystack(query)
    for phrase in doc.identifying_phrases():
        normalized = " ".join(tokenize(phrase))
        if normalized and f" {normalized} " in haystack:
            score += config.PHRASE_MATCH_BONUS

    score *= config.STATUS_MULTIPLIERS.get(doc.status, config.DEFAULT_STATUS_MULTIPLIER)

    return SearchResult(doc=doc, score=round(score, 2), matched_fields=matched_fields)


def search(docs: Iterable[AgendaDoc], query: str, limit: int) -> list[SearchResult]:
    """Rank docs against a query, best first. Ties break on id for stable output."""
    results = [result for result in (score_doc(doc, query) for doc in docs) if result]
    results.sort(key=lambda result: (-result.score, result.doc.id))
    return results[:limit]
