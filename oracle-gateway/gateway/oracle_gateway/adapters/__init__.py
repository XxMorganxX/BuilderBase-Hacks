"""Adapter registry.

Adding an agent is: one module here, one entry in ADAPTERS, one fixture, one
test. No schema change, no ingest change (PRINCIPLES P2).
"""

from __future__ import annotations

from .base import AdapterError, ParsedBatch, SessionAdapter
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter

ADAPTERS: dict[str, SessionAdapter] = {
    ClaudeCodeAdapter.agent_kind: ClaudeCodeAdapter(),
    CodexAdapter.agent_kind: CodexAdapter(),
}


def get_adapter(agent_kind: str) -> SessionAdapter:
    try:
        return ADAPTERS[agent_kind]
    except KeyError:
        raise AdapterError(
            f"no adapter for agent_kind '{agent_kind}'; known: {', '.join(sorted(ADAPTERS))}. "
            "Agents that convert themselves can POST canonical JSON to /v1/ingest."
        ) from None


__all__ = ["ADAPTERS", "AdapterError", "ParsedBatch", "SessionAdapter", "get_adapter"]
