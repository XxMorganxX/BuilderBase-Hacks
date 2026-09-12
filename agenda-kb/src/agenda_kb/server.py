"""The MCP surface: three read-only tools over the agenda knowledge base.

This module is deliberately thin. It formats and it wires — every decision about what a
doc *is* or which doc is relevant lives behind the ``AgendaStore`` contract, and every
string the agent reads lives in ``config``.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from agenda_kb import config
from agenda_kb.models import AgendaDoc, SearchResult
from agenda_kb.store import create_store

mcp = MCPServer(
    name=config.SERVER_NAME,
    instructions=config.SERVER_INSTRUCTIONS,
)

_store = create_store()

#: Every tool here reads; none of them writes. Stating that lets a client skip
#: confirmation prompts it would otherwise raise.
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _current_store():
    """The store, refreshed if configured to be — see RELOAD_BEFORE_EVERY_QUERY."""
    if config.RELOAD_BEFORE_EVERY_QUERY:
        _store.reload()
    return _store


# --- Formatting ---------------------------------------------------------------------
# Pure functions: docs in, agent-readable text out. Kept separate from the tools so the
# output can be tested without standing up a server.


def format_catalogue(docs: list[AgendaDoc]) -> str:
    if not docs:
        return f"The agenda knowledge base is empty ({config.KB_DIR} has no valid docs)."

    lines = [f"{len(docs)} agenda doc(s) in the knowledge base:", ""]
    for doc in docs:
        marker = "" if doc.is_current else f" [{doc.status.upper()}]"
        lines.append(f"## {doc.title}{marker}")
        lines.append(f"- id: `{doc.id}`")
        lines.append(f"- project: {doc.project}")
        lines.append(f"- department: {doc.department} / {doc.team}")
        lines.append(f"- owner: {doc.owner}")
        lines.append(f"- period: {doc.period} (status: {doc.status}, updated {doc.updated})")
        if doc.aliases:
            lines.append(f"- also called: {', '.join(doc.aliases)}")
        if doc.summary:
            lines.append(f"- summary: {doc.summary}")
        lines.append("")
    lines.append("Call get_agenda_doc with an id to read one in full.")
    return "\n".join(lines)


def format_results(results: list[SearchResult], query: str) -> str:
    if not results:
        return (
            f"No agenda doc matches {query!r}.\n\n"
            "Try a project, product, department, team, or manager name, or call "
            "list_agenda_docs to see everything available. Do not infer an agenda for a "
            "project that has no doc — say that none is on file."
        )

    lines = [f"{len(results)} agenda doc(s) matching {query!r}, best match first:", ""]
    for rank, result in enumerate(results, start=1):
        doc = result.doc
        marker = "" if doc.is_current else f" [{doc.status.upper()} — superseded]"
        lines.append(f"{rank}. **{doc.title}**{marker} — id `{doc.id}`")
        lines.append(f"   - {doc.project} · {doc.department} / {doc.team} · owner {doc.owner}")
        lines.append(f"   - period {doc.period}, updated {doc.updated}")
        if doc.summary:
            lines.append(f"   - {doc.summary}")
        lines.append(
            f"   - relevance {result.score} (matched on: {', '.join(result.matched_fields)})"
        )
        lines.append("")
    if _is_ambiguous(results):
        lines.append(config.AMBIGUOUS_MATCH_NOTICE)
    else:
        lines.append(
            f"Call get_agenda_doc(\"{results[0].doc.id}\") to read the top match in full "
            "before answering."
        )
    return "\n".join(lines)


def _is_ambiguous(results: list[SearchResult]) -> bool:
    """True when the runner-up is close enough that the winner is effectively arbitrary."""
    if len(results) < 2 or results[0].score <= 0:
        return False
    return results[1].score / results[0].score >= config.CLOSE_MATCH_RATIO


def format_doc(doc: AgendaDoc) -> str:
    header = [
        f"# {doc.title}",
        "",
        f"- id: `{doc.id}`",
        f"- project: {doc.project}",
        f"- department: {doc.department} / {doc.team}",
        f"- owner: {doc.owner}",
        f"- period: {doc.period}",
        f"- status: {doc.status}",
        f"- updated: {doc.updated}",
    ]
    if doc.aliases:
        header.append(f"- also called: {', '.join(doc.aliases)}")
    if doc.tags:
        header.append(f"- tags: {', '.join(doc.tags)}")
    if not doc.is_current:
        header += [
            "",
            f"> This agenda is **{doc.status}** and may have been superseded. Say so if you "
            "rely on it.",
        ]
    return "\n".join(header + ["", "---", "", doc.body])


# --- Tools --------------------------------------------------------------------------


@mcp.tool(description=config.LIST_TOOL_DESCRIPTION, annotations=_READ_ONLY)
def list_agenda_docs() -> str:
    return format_catalogue(_current_store().list_docs())


@mcp.tool(description=config.FIND_TOOL_DESCRIPTION, annotations=_READ_ONLY)
def find_agenda_doc(query: str, limit: int = config.DEFAULT_SEARCH_LIMIT) -> str:
    return format_results(_current_store().search(query, limit=limit), query)


@mcp.tool(description=config.GET_TOOL_DESCRIPTION, annotations=_READ_ONLY)
def get_agenda_doc(doc_id: str) -> str:
    return format_doc(_current_store().get_doc(doc_id))


@mcp.resource(
    config.RESOURCE_URI_TEMPLATE,
    description="One agenda doc in full, addressed by its id.",
    mime_type="text/markdown",
)
def agenda_doc_resource(doc_id: str) -> str:
    return format_doc(_current_store().get_doc(doc_id))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
