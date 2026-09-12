"""Splitting an agenda doc into the units that get embedded.

One chunk per second-level heading, because that is how these docs are actually organized:
Mission, Agenda for the period, Success metrics, Dependencies, Explicitly not doing. Each
answers a different question, so each deserves its own vector. A single whole-doc vector
averages all five into a centroid that is about "a team doing things".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agenda_kb import config
from agenda_kb.models import AgendaDoc

__all__ = ["Chunk", "split_doc"]


@dataclass(frozen=True)
class Chunk:
    """One section of one agenda doc."""

    doc_id: str
    doc_title: str
    ordinal: int
    heading: str
    text: str

    @property
    def chunk_id(self) -> str:
        """Stable and derivable, so re-embedding overwrites rather than accumulating."""
        return f"{self.doc_id}#{self.ordinal}"

    @property
    def embed_text(self) -> str:
        """The text actually handed to the model — the section, prefixed with the doc's
        title and its own heading so an isolated section keeps its identity."""
        return config.CHUNK_CONTEXT_TEMPLATE.format(
            title=self.doc_title, heading=self.heading, text=self.text
        )


_ITEM_START = re.compile(config.CHUNK_ITEM_PATTERN)


def _split_on_items(text: str) -> list[str]:
    """Break a long section into its top-level list items.

    Returns a single-element list when the section is short, has no list, or has only one
    item — splitting those buys nothing and costs the item its surrounding context.
    """
    if len(text) <= config.CHUNK_LONG_SECTION_CHARS:
        return [text]

    lead: list[str] = []
    items: list[list[str]] = []
    for line in text.splitlines():
        if _ITEM_START.match(line):
            items.append([line])
        elif items:
            items[-1].append(line)
        else:
            lead.append(line)

    if len(items) < 2:
        return [text]

    pieces = [preamble for preamble in ["\n".join(lead).strip()] if len(preamble) >= config.CHUNK_MIN_CHARS]
    pieces.extend(
        item for item in ("\n".join(lines).strip() for lines in items)
        if len(item) >= config.CHUNK_MIN_CHARS
    )
    return pieces or [text]


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def split_doc(doc: AgendaDoc) -> list[Chunk]:
    """Split a doc on its ``##`` headings. Never returns an empty list for a doc with a
    body: a doc with no headings at all is one chunk, which is the honest representation
    of it rather than a silent gap in the index."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in doc.body.splitlines():
        if line.startswith(config.CHUNK_HEADING_PREFIX):
            sections.append((line[len(config.CHUNK_HEADING_PREFIX) :].strip(), []))
        else:
            sections[-1][1].append(line)

    fallback_heading = _first_heading(doc.body) or doc.title
    chunks: list[Chunk] = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if len(text) < config.CHUNK_MIN_CHARS:
            continue
        for piece in _split_on_items(text):
            chunks.append(
                Chunk(
                    doc_id=doc.id,
                    doc_title=doc.title,
                    ordinal=len(chunks),
                    heading=heading or fallback_heading,
                    text=piece,
                )
            )

    if not chunks and doc.body.strip():
        chunks.append(
            Chunk(
                doc_id=doc.id,
                doc_title=doc.title,
                ordinal=0,
                heading=fallback_heading,
                text=doc.body.strip(),
            )
        )
    return chunks
