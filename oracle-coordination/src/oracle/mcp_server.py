"""Local MCP tools: no product-specific agent dependency or hidden transcript reader."""

from mcp.server.fastmcp import FastMCP

from .protocol import ContextPacket, EventType, FactInput, InterfaceReport, NegotiationResponse, Registration


# Specification section 17 names the tools with dots (oracle.gate). The MCP tool-name grammar used by
# Claude Code / the Claude API is ^[a-zA-Z0-9_-]{1,128}$, so dotted names cannot be exposed; the wire
# names use underscores (oracle_gate) and map one-to-one onto the specified names.
TOOL_NAMES = [
    "oracle_register",
    "oracle_get_context",
    "oracle_report_task",
    "oracle_report_progress",
    "oracle_report_assumption",
    "oracle_report_decision",
    "oracle_report_interface",
    "oracle_report_question",
    "oracle_checkpoint",
    "oracle_gate",
    "oracle_get_messages",
    "oracle_reply",
    "oracle_ack_pause",
    "oracle_request_resume",
]


def create_mcp(bridge):
    mcp = FastMCP(
        "ORACLE Bridge",
        instructions=(
            "Register before work. Read context at task boundaries. Call oracle_gate before shared-contract changes. "
            "PAUSE means finish only the current safe atomic operation, persist state, then call oracle_ack_pause. "
            "Do not begin implementation until RESUME. Read messages and acknowledge each final decision using oracle_reply. "
            "Only report compact selected project facts; keep full transcripts local."
        ),
    )

    @mcp.tool(name="oracle_register")
    async def register(registration: Registration) -> dict:
        """Register the session with its human, machine, repository, task, and shared contracts."""
        return await bridge.register(registration)

    @mcp.tool(name="oracle_get_context")
    async def get_context(subjects: list[str] | None = None) -> dict:
        """Read authorized relevant requirements and decisions with provenance."""
        return {"context": await bridge.context(subjects)}

    @mcp.tool(name="oracle_report_task")
    async def report_task(task: str) -> dict:
        """Update the current implementation task."""
        return await bridge.emit(EventType.TASK_UPDATE, {"task": task})

    @mcp.tool(name="oracle_report_progress")
    async def report_progress(packet: ContextPacket) -> dict:
        """Publish a compact checkpoint, preserving human intent separately from interpretation."""
        return await bridge.checkpoint(packet)

    @mcp.tool(name="oracle_report_assumption")
    async def report_assumption(fact: FactInput) -> dict:
        """Report an assumption; it cannot become an authoritative human decision."""
        return await bridge.emit(EventType.ASSUMPTION_UPDATE, fact.model_dump(mode="json"))

    @mcp.tool(name="oracle_report_decision")
    async def report_decision(fact: FactInput) -> dict:
        """Report a coding-agent choice, subject to higher-authority contracts."""
        return await bridge.emit(EventType.DECISION_UPDATE, fact.model_dump(mode="json"))

    @mcp.tool(name="oracle_report_interface")
    async def report_interface(interface: InterfaceReport) -> dict:
        """Declare a produced or consumed interface and its exact contract fields."""
        return await bridge.emit(EventType.INTERFACE_UPDATE, interface.model_dump(mode="json"))

    @mcp.tool(name="oracle_report_question")
    async def report_question(subject: str, question: str, predicate: str = "definition") -> dict:
        """Ask the authorized project owner to resolve a consequential missing specification."""
        return await bridge.emit(
            EventType.QUESTION_UPDATE, {"subject": subject, "predicate": predicate, "question": question}
        )

    @mcp.tool(name="oracle_checkpoint")
    async def checkpoint() -> dict:
        """Report Git metadata without file contents and retrieve pending Oracle commands."""
        return await bridge.checkpoint()

    @mcp.tool(name="oracle_gate")
    async def gate(action: str, target: str, proposed_change: str, revision: str | None = None) -> dict:
        """Authorize this exact shared-contract action. Proceed only on ALLOW; offline is blocked."""
        return await bridge.gate(action, target, proposed_change, revision)

    @mcp.tool(name="oracle_get_messages")
    async def get_messages() -> dict:
        """Read durable pause, mediation, final-decision, and resume commands."""
        return await bridge.poll()

    @mcp.tool(name="oracle_reply")
    async def reply(response: NegotiationResponse | None = None, decision_id: str | None = None) -> dict:
        """Submit one bounded negotiation response OR acknowledge an understood final decision."""
        if (response is None) == (decision_id is None):
            raise ValueError("Provide exactly one of response or decision_id")
        if decision_id:
            return await bridge.emit(EventType.DECISION_ACK, {"decision_id": decision_id})
        return await bridge.emit(EventType.MEDIATION_RESPONSE, response.model_dump(mode="json"))

    @mcp.tool(name="oracle_ack_pause")
    async def ack_pause(conflict_id: str | None = None, question_id: str | None = None) -> dict:
        """Confirm that work is saved and the session has reached a safe checkpoint."""
        if bool(conflict_id) == bool(question_id):
            raise ValueError("Provide exactly one pause reference")
        return await bridge.emit(
            EventType.PAUSE_ACK, {"conflict_id": conflict_id, "question_id": question_id}
        )

    @mcp.tool(name="oracle_request_resume")
    async def request_resume() -> dict:
        """Check whether all blocking issues and required decision acknowledgements are resolved."""
        return await bridge.emit(EventType.RESUME_REQUEST, {})

    return mcp
