"""The shapes that flow between the store, retrieval, and the MCP surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class AgendaDocNotFound(LookupError):
    """Raised when no agenda doc matches the requested id or alias."""


@dataclass(frozen=True)
class AgendaDoc:
    """One manager-submitted agenda doc: its frontmatter, its prose, and its origin."""

    id: str
    title: str
    project: str
    department: str
    team: str
    owner: str
    status: str
    period: str
    updated: str
    body: str
    summary: str
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    path: Optional[Path] = None

    @property
    def is_current(self) -> bool:
        return self.status == "active"

    def searchable_fields(self) -> dict[str, str]:
        """Field name -> the text retrieval should match against.

        Keys must line up with ``config.FIELD_WEIGHTS``; a field absent from the weights
        is simply never scored.
        """
        return {
            "id": self.id,
            "aliases": " ".join(self.aliases),
            "project": self.project,
            "title": self.title,
            "team": self.team,
            "department": self.department,
            "owner": self.owner,
            "tags": " ".join(self.tags),
            "period": self.period,
            "status": self.status,
            "body": self.body,
        }

    def identifying_phrases(self) -> set[str]:
        """The multi-word names a person would use to mean *this* doc specifically.

        A verbatim hit on one of these ("product 1", "atlas checkout") is far stronger
        evidence than the same words scattered across a document.
        """
        return {self.id.replace("-", " "), self.title, self.project, *self.aliases}

    def to_catalogue_entry(self) -> dict:
        """The compact form used in listings and search results — everything needed to
        choose a doc, without the cost of its full text."""
        return {
            "id": self.id,
            "title": self.title,
            "project": self.project,
            "department": self.department,
            "team": self.team,
            "owner": self.owner,
            "status": self.status,
            "period": self.period,
            "aliases": self.aliases,
            "tags": self.tags,
            "updated": self.updated,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class SearchResult:
    """A scored match, plus why it matched — so a caller can judge the ranking."""

    doc: AgendaDoc
    score: float
    matched_fields: list[str]
