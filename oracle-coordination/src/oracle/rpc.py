"""Participant RPC allowlist. Host human/admin actions are separate CLI operations."""

from .policy import OracleError


def dispatch(oracle, principal, request):
    if not isinstance(request, dict):
        raise OracleError("INVALID_REQUEST", "Request must be an object")
    op = request.get("op")
    if op == "health":
        return {"status": "ok", "protocol_version": 1}
    if op == "ingest":
        return oracle.ingest(principal, request["event"])
    if op == "poll":
        return oracle.poll(principal, request["session_id"], request.get("after", 0))
    if op == "ack_delivery":
        return oracle.acknowledge_delivery(principal, request["session_id"], request["cursor"])
    if op == "context":
        return oracle.context(principal, request["session_id"], subjects=request.get("subjects"))
    if op == "status":
        return oracle.status(principal, request["project_id"])
    raise OracleError("UNSUPPORTED_OPERATION", "Operation is not exposed to coding sessions")
