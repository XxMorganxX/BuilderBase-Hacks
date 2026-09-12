"""The single write path into the log.

Everything that lands in the database goes through ingest_batch: the canonical
endpoint, every adapter, and anything added later. Events are append-only
(PRINCIPLES P1) and re-sending is always safe (P4).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from . import db
from .auth import User
from .config import SETTINGS
from .models import EventIn, IngestResult, SessionIn

log = logging.getLogger("oracle.ingest")


class IngestTooLarge(Exception):
    def __init__(self, count: int) -> None:
        super().__init__(
            f"{count} events exceeds the {SETTINGS.max_events_per_batch} per-batch limit"
        )
        self.count = count


def _event_row(event: EventIn) -> dict[str, Any]:
    """A JSON-safe row for the bulk insert. Raw is kept verbatim (P3)."""
    return {
        "external_id": event.external_id,
        "parent_external_id": event.parent_external_id,
        "seq": event.seq,
        "type": event.type,
        "role": event.role,
        "content": event.model_dump(mode="json")["content"],
        "text": event.content_text(),
        "model": event.model,
        "usage": event.usage,
        "occurred_at": event.occurred_at.isoformat(),
        "metadata": event.metadata or {},
        "raw": event.raw,
    }


def _dedupe(events: list[EventIn]) -> tuple[list[EventIn], int]:
    """Collapse repeated external ids inside one batch, keeping the first."""
    seen: set[str] = set()
    kept: list[EventIn] = []
    for event in events:
        if event.external_id in seen:
            continue
        seen.add(event.external_id)
        kept.append(event)
    return kept, len(events) - len(kept)


async def ingest_batch(
    user: User,
    session: SessionIn,
    events: list[EventIn],
    skipped: int = 0,
) -> IngestResult:
    received = len(events)
    if received > SETTINGS.max_events_per_batch:
        raise IngestTooLarge(received)

    unique, in_batch_duplicates = _dedupe(events)
    rows = [_event_row(event) for event in unique]
    latest: datetime | None = max((e.occurred_at for e in unique), default=None)

    session_id, inserted = await db.ingest_batch(
        user_id=user.id,
        session_row=session.model_dump(mode="python"),
        event_rows=rows,
        latest_occurred_at=latest,
    )

    result = IngestResult(
        session_id=session_id,
        received=received,
        inserted=inserted,
        duplicates=received - inserted,
        skipped=skipped,
    )
    log.info(
        "ingest user=%s kind=%s session=%s received=%d inserted=%d duplicates=%d "
        "in_batch_duplicates=%d skipped=%d",
        user.email,
        session.agent_kind,
        session_id,
        received,
        inserted,
        result.duplicates,
        in_batch_duplicates,
        skipped,
    )
    return result
