"""The canonical session/event contract.

These models ARE the contract documented in docs/PLAN.md section 4 (PRINCIPLES P8).
If they change, update that section in the same commit. Nothing vendor-specific
belongs in this file: vendor formats stop at the adapters (P2).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import SETTINGS

EventType = Literal[
    "user_message",
    "assistant_message",
    "thinking",
    "tool_call",
    "tool_result",
    "system",
    "summary",
    "other",
]
Role = Literal["user", "assistant", "system", "tool"]

# Known agent kinds. Unknown-but-well-formed slugs are accepted: a new agent must
# not require a gateway change (P2). The adapter registry is what gates /ingest/raw.
KNOWN_AGENT_KINDS = ("claude_code", "codex", "cursor", "custom")


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise to timezone-aware UTC so asyncpg never guesses a timezone."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _block_text(block: Any) -> str:
    """Flatten one canonical content block to text (PLAN 4.3)."""
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text") or "")
    if kind == "thinking":
        return str(block.get("thinking") or "")
    if kind == "tool_use":
        name = block.get("name") or "tool"
        try:
            args = json.dumps(block.get("input") or {}, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            args = str(block.get("input"))
        return f"{name}({args})"
    if kind == "tool_result":
        content = block.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(t for t in (_block_text(b) for b in content) if t)
        return ""
    return ""


def derive_text(content: list[Any], limit: int | None = None) -> str:
    """Flatten canonical content blocks into searchable text (PLAN 4.3)."""
    limit = SETTINGS.content_text_max if limit is None else limit
    parts = [t for t in (_block_text(b) for b in content or []) if t]
    text = "\n".join(parts)
    return text[:limit]


class SessionIn(BaseModel):
    """Canonical session header (PLAN 4.1)."""

    model_config = ConfigDict(extra="ignore")

    external_id: str = Field(min_length=1, max_length=512)
    agent_kind: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    agent_version: str | None = Field(default=None, max_length=128)
    workspace: str | None = Field(default=None, max_length=2048)
    repo: str | None = Field(default=None, max_length=1024)
    branch: str | None = Field(default=None, max_length=512)
    title: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("started_at", "ended_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return _as_utc(v)

    @field_validator("title")
    @classmethod
    def _clamp_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v[: SETTINGS.title_max] if v else None


class EventIn(BaseModel):
    """Canonical event (PLAN 4.2). One immutable line of a session."""

    model_config = ConfigDict(extra="ignore")

    external_id: str = Field(min_length=1, max_length=512)
    parent_external_id: str | None = Field(default=None, max_length=512)
    seq: int | None = None
    type: EventType
    role: Role | None = None
    content: list[Any] = Field(default_factory=list)
    text: str | None = None
    model: str | None = Field(default=None, max_length=256)
    usage: dict[str, Any] | None = None
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: Any = None

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _as_utc(v)  # type: ignore[return-value]

    def content_text(self) -> str:
        """Text for search: what the adapter gave us, else derived from content."""
        if self.text:
            return self.text[: SETTINGS.content_text_max]
        return derive_text(self.content)


class IngestBatch(BaseModel):
    """One ingest request body (PLAN 4.4)."""

    model_config = ConfigDict(extra="ignore")

    session: SessionIn
    events: list[EventIn] = Field(default_factory=list)


class IngestResult(BaseModel):
    session_id: UUID
    received: int
    inserted: int
    duplicates: int
    skipped: int = 0


class UserOut(BaseModel):
    id: UUID
    email: str
    display_name: str


class SessionOut(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str | None = None
    agent_kind: str
    agent_version: str | None = None
    external_session_id: str
    workspace: str | None = None
    repo: str | None = None
    branch: str | None = None
    title: str | None = None
    started_at: datetime | None = None
    last_event_at: datetime | None = None
    ended_at: datetime | None = None
    event_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventOut(BaseModel):
    id: int
    session_id: UUID
    user_id: UUID | None = None
    seq: int
    external_event_id: str
    parent_external_id: str | None = None
    event_type: EventType
    role: Role | None = None
    content: list[Any] = Field(default_factory=list)
    content_text: str | None = None
    model: str | None = None
    usage: dict[str, Any] | None = None
    occurred_at: datetime
    ingested_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
