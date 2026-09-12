"""The adapter contract.

One adapter per agent_kind turns a vendor's native session lines into the
canonical contract. Vendor knowledge stops here (PRINCIPLES P2): nothing
outside this package may mention a vendor format.

Two rules every adapter follows:
  * Never raise on a line it does not understand. Count it in `skipped`.
    A vendor can change its format mid-session and ingestion keeps working.
  * Always fill `raw`, `text` and `occurred_at`, so the server can re-project
    a better canonical form later without asking any laptop to re-ship (P3).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from pydantic import BaseModel, Field

from ..models import EventIn, SessionIn


class AdapterError(Exception):
    """The lines cannot be attributed to a session. A 400, not a 500."""


class ParsedBatch(BaseModel):
    session: SessionIn
    events: list[EventIn] = Field(default_factory=list)
    skipped: int = 0


class SessionAdapter(Protocol):
    agent_kind: str

    def parse(self, lines: Iterable[str]) -> ParsedBatch: ...


def parse_json_lines(lines: Iterable[str]) -> tuple[list[tuple[dict[str, Any], str]], int]:
    """Parse NDJSON, returning (object, original line) pairs and a skip count."""
    parsed: list[tuple[dict[str, Any], str]] = []
    skipped = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except (ValueError, TypeError):
            skipped += 1
            continue
        if not isinstance(obj, dict):
            skipped += 1
            continue
        parsed.append((obj, stripped))
    return parsed, skipped


def parse_timestamp(value: Any) -> datetime | None:
    """Accept the ISO-8601 spellings both vendors emit, including a Z suffix."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


_OPENING_TAG = re.compile(r"\s*<([A-Za-z][A-Za-z0-9._-]*)>")


def is_injected_context(text: str) -> bool:
    """True when a user-role message is harness chrome rather than a person talking.

    Every agent injects context into the conversation as user messages, wrapped
    in a pseudo-tag: Claude Code writes <command-name>/model</command-name> and
    <local-command-stdout>...</local-command-stdout>; Codex writes
    <recommended_plugins>...</recommended_plugins> and <environment_context>.
    Only some of them are flagged in the vendor's own metadata, so recognise the
    shape instead: an opening pseudo-tag that is closed later in the same text.

    Requiring the closing tag is what keeps a real question safe. "<div> is not
    rendering" opens a tag and never closes it, so it stays a human prompt.

    These messages are still stored as events (P3). This only decides what is
    allowed to become a session title.
    """
    match = _OPENING_TAG.match(text)
    return bool(match) and f"</{match.group(1)}>" in text


def compact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Drop None values so event metadata stays small and queryable."""
    return {k: v for k, v in mapping.items() if v is not None}


def as_json_input(value: Any) -> dict[str, Any]:
    """Tool arguments arrive as JSON strings. Keep a dict; never lose the text."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return {"input": value}
        return decoded if isinstance(decoded, dict) else {"input": decoded}
    if value is None:
        return {}
    return {"input": value}
