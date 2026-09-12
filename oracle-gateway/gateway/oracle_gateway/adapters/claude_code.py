"""Claude Code: ~/.claude/projects/<slug>/<sessionId>.jsonl (PLAN 7.1).

One JSON object per line. Only `user`, `assistant` and `summary` lines carry
conversation; the rest (mode, permission-mode, bridge-session, attachment,
file-history-snapshot, last-prompt, atis-latch, ...) is UI chrome and is
counted, not stored.

Claude Code writes each API content block on its own line, so one model turn
can be three events (thinking, text, tool_use) sharing a requestId. They stay
separate here; readers regroup on metadata.request_id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from ..config import SETTINGS
from ..models import EventIn, SessionIn, derive_text
from .base import (
    AdapterError,
    ParsedBatch,
    compact,
    is_injected_context,
    parse_json_lines,
    parse_timestamp,
)

CONVERSATION_TYPES = frozenset({"user", "assistant", "summary"})

# Claude Code generates its own session title and writes it on an `ai-title`
# line. When present it beats anything we could infer.
TITLE_LINE_TYPE = "ai-title"


def _blocks(content: Any) -> list[Any]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    if isinstance(content, dict):
        return [content]
    return []


def _block_types(blocks: list[Any]) -> set[str]:
    return {b.get("type") for b in blocks if isinstance(b, dict)}


def _classify(line_type: str, blocks: list[Any]) -> str:
    kinds = _block_types(blocks)
    if line_type == "user":
        return "tool_result" if "tool_result" in kinds else "user_message"
    if line_type == "assistant":
        if kinds and kinds <= {"thinking", "redacted_thinking"}:
            return "thinking"
        if "tool_use" in kinds:
            return "tool_call"
        return "assistant_message"
    return "summary"


class ClaudeCodeAdapter:
    agent_kind = "claude_code"

    def parse(self, lines: Iterable[str]) -> ParsedBatch:
        records, skipped = parse_json_lines(lines)

        session_id: str | None = None
        workspace = branch = version = entrypoint = None
        events: list[EventIn] = []
        vendor_title: str | None = None
        prompt_title: str | None = None
        earliest: datetime | None = None
        last_seen: datetime | None = None

        for obj, raw_line in records:
            # Chrome lines still carry the session id; harvest it before dropping.
            session_id = session_id or obj.get("sessionId")
            line_type = obj.get("type")
            if line_type == TITLE_LINE_TYPE and obj.get("aiTitle"):
                vendor_title = str(obj["aiTitle"])
            if line_type not in CONVERSATION_TYPES:
                skipped += 1
                continue

            workspace = workspace or obj.get("cwd")
            branch = branch or obj.get("gitBranch")
            version = version or obj.get("version")
            entrypoint = entrypoint or obj.get("entrypoint")

            occurred_at = parse_timestamp(obj.get("timestamp"))
            message = obj.get("message") if isinstance(obj.get("message"), dict) else {}

            if line_type == "summary":
                # Summary lines carry neither uuid nor timestamp: derive both.
                leaf = obj.get("leafUuid") or obj.get("messageId")
                if not leaf:
                    skipped += 1
                    continue
                external_id = f"summary:{leaf}"
                blocks = [{"type": "text", "text": str(obj.get("summary") or "")}]
                occurred_at = occurred_at or last_seen or earliest or datetime.now(timezone.utc)
                role = None
            else:
                external_id = obj.get("uuid")
                if not external_id:
                    skipped += 1
                    continue
                if occurred_at is None:
                    skipped += 1
                    continue
                blocks = _blocks(message.get("content"))
                role = message.get("role")

            event_type = _classify(line_type, blocks)

            if occurred_at is not None:
                earliest = occurred_at if earliest is None else min(earliest, occurred_at)
                last_seen = occurred_at if last_seen is None else max(last_seen, occurred_at)

            if (
                prompt_title is None
                and line_type == "user"
                and not obj.get("isMeta")
                and event_type == "user_message"
            ):
                # Check the whole message: truncating first would cut off the
                # closing tag that marks it as injected context.
                candidate = derive_text(blocks).strip()
                if candidate and not is_injected_context(candidate):
                    prompt_title = candidate[: SETTINGS.title_max]

            events.append(
                EventIn(
                    external_id=external_id,
                    parent_external_id=obj.get("parentUuid"),
                    type=event_type,
                    role=role if role in ("user", "assistant", "system", "tool") else None,
                    content=blocks,
                    text=derive_text(blocks),
                    model=message.get("model"),
                    usage=message.get("usage") if isinstance(message.get("usage"), dict) else None,
                    occurred_at=occurred_at,
                    metadata=compact(
                        {
                            "is_sidechain": obj.get("isSidechain"),
                            "is_meta": obj.get("isMeta"),
                            "prompt_id": obj.get("promptId"),
                            "request_id": obj.get("requestId"),
                            "api_block_index": obj.get("apiBlockIndex"),
                            "message_id": message.get("id"),
                        }
                    ),
                    raw=obj,
                )
            )

        if not session_id:
            raise AdapterError("no sessionId found in these lines")

        session = SessionIn(
            external_id=session_id,
            agent_kind=self.agent_kind,
            agent_version=version,
            workspace=workspace,
            branch=branch,
            title=vendor_title or prompt_title,
            started_at=earliest,
            metadata=compact({"entrypoint": entrypoint}),
        )
        return ParsedBatch(session=session, events=events, skipped=skipped)
