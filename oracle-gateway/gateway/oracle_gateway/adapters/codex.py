"""Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl (PLAN 7.2).

Every line is {timestamp, ordinal, type, payload}. The conversation lives in
`response_item` lines; `session_meta` is the header the shipper guarantees is
present in every batch (decision D10). `turn_context` and `world_state` are
carried forward as context for the events that follow them.

Note the deliberate asymmetry with the Claude Code adapter: Codex tool output
is role "tool", Claude Code's is role "user", because each vendor says so.
Readers key off event_type, which is identical across both.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from ..config import SETTINGS
from ..models import EventIn, SessionIn, derive_text
from .base import (
    AdapterError,
    ParsedBatch,
    as_json_input,
    compact,
    is_injected_context,
    parse_json_lines,
    parse_timestamp,
)

TEXTUAL_BLOCKS = frozenset({"input_text", "output_text", "text", "summary_text"})
CALL_TYPES = frozenset({"function_call", "custom_tool_call"})
OUTPUT_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})
ROLE_TO_TYPE = {
    "user": ("user_message", "user"),
    "assistant": ("assistant_message", "assistant"),
    "developer": ("system", "system"),
    "system": ("system", "system"),
}
ASSISTANT_SIDE = frozenset({"assistant_message", "thinking", "tool_call"})


def _text_blocks(content: Any) -> list[Any]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks: list[Any] = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in TEXTUAL_BLOCKS:
            blocks.append({"type": "text", "text": str(item.get("text") or "")})
        else:
            blocks.append(item)  # images and anything new: pass through (P3)
    return blocks


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class CodexAdapter:
    agent_kind = "codex"

    def parse(self, lines: Iterable[str]) -> ParsedBatch:
        records, skipped = parse_json_lines(lines)

        session: SessionIn | None = None
        session_external_id: str | None = None
        turn_id: str | None = None
        model: str | None = None
        events: list[EventIn] = []
        earliest: datetime | None = None
        title: str | None = None

        for obj, _raw_line in records:
            line_type = obj.get("type")
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            occurred_at = parse_timestamp(obj.get("timestamp"))

            if line_type == "session_meta":
                session_external_id = payload.get("session_id") or payload.get("id")
                if session_external_id:
                    started = parse_timestamp(payload.get("timestamp")) or occurred_at
                    session = SessionIn(
                        external_id=session_external_id,
                        agent_kind=self.agent_kind,
                        agent_version=payload.get("cli_version"),
                        workspace=payload.get("cwd"),
                        started_at=started,
                        metadata=compact(
                            {
                                "originator": payload.get("originator"),
                                "source": payload.get("source"),
                                "model_provider": payload.get("model_provider"),
                            }
                        ),
                    )
                skipped += 1
                continue

            if line_type == "turn_context":
                turn_id = payload.get("turn_id") or turn_id
                skipped += 1
                continue

            if line_type == "world_state":
                state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
                mode = state.get("collaboration_mode") if isinstance(state, dict) else None
                if isinstance(mode, dict) and mode.get("model"):
                    model = mode["model"]
                skipped += 1
                continue

            if line_type != "response_item" or occurred_at is None:
                skipped += 1
                continue

            item_type = payload.get("type")
            ordinal = obj.get("ordinal")

            if item_type == "message":
                event_type, role = ROLE_TO_TYPE.get(payload.get("role"), ("other", None))
                blocks = _text_blocks(payload.get("content"))
            elif item_type == "reasoning":
                event_type, role = "thinking", "assistant"
                thought = "\n".join(
                    str(s.get("text") or "")
                    for s in payload.get("summary") or []
                    if isinstance(s, dict)
                ).strip()
                blocks = [{"type": "thinking", "thinking": thought}]
            elif item_type in CALL_TYPES:
                event_type, role = "tool_call", "assistant"
                raw_input = payload.get("arguments") if "arguments" in payload else payload.get("input")
                blocks = [
                    {
                        "type": "tool_use",
                        "id": payload.get("call_id") or payload.get("id"),
                        "name": payload.get("name") or "tool",
                        "input": as_json_input(raw_input),
                    }
                ]
            elif item_type in OUTPUT_TYPES:
                event_type, role = "tool_result", "tool"
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": payload.get("call_id"),
                        "content": _as_text(payload.get("output")),
                    }
                ]
            else:
                event_type, role, blocks = "other", None, []

            # Codex rollouts carry no title: the first real user prompt is it (PLAN 4.1).
            if title is None and event_type == "user_message":
                # Check the whole message: truncating first would cut off the
                # closing tag that marks it as injected context.
                candidate = derive_text(blocks).strip()
                if candidate and not is_injected_context(candidate):
                    title = candidate[: SETTINGS.title_max]

            external_id = payload.get("id") or f"{session_external_id or 'codex'}:{ordinal}"
            earliest = occurred_at if earliest is None else min(earliest, occurred_at)

            events.append(
                EventIn(
                    external_id=str(external_id),
                    seq=ordinal if isinstance(ordinal, int) else None,
                    type=event_type,
                    role=role,
                    content=blocks,
                    text=derive_text(blocks),
                    model=model if event_type in ASSISTANT_SIDE else None,
                    occurred_at=occurred_at,
                    metadata=compact({"turn_id": turn_id, "item_type": item_type}),
                    raw=obj,
                )
            )

        if session is None:
            raise AdapterError(
                "no session_meta line in these lines: the shipper must include the "
                "rollout header in every batch"
            )
        updates: dict[str, object] = {}
        if session.started_at is None and earliest is not None:
            updates["started_at"] = earliest
        if session.title is None and title is not None:
            updates["title"] = title
        if updates:
            session = session.model_copy(update=updates)

        return ParsedBatch(session=session, events=events, skipped=skipped)
