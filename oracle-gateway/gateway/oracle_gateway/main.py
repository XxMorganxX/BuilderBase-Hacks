"""HTTP surface. Routes only: every decision lives in ingest, db or adapters."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import db, ingest
from .adapters import ADAPTERS, AdapterError, get_adapter
from .auth import User, current_user
from .config import SETTINGS
from .models import (
    EventOut,
    IngestBatch,
    IngestResult,
    SessionOut,
    UserOut,
)

logging.basicConfig(
    level=getattr(logging, SETTINGS.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("oracle.gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    log.info("gateway ready adapters=%s", ",".join(sorted(ADAPTERS)))
    try:
        yield
    finally:
        await db.close()


app = FastAPI(
    title="Oracle session gateway",
    version="0.1.0",
    summary="Ingests agent sessions from any vendor into one append-only log.",
    lifespan=lifespan,
)


def _error(status: int, message: str, detail: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message, "detail": detail})


@app.exception_handler(HTTPException)
async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return _error(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(422, "request does not match the canonical contract", exc.errors())


@app.exception_handler(AdapterError)
async def _adapter_error(_: Request, exc: AdapterError) -> JSONResponse:
    return _error(400, str(exc))


@app.exception_handler(ingest.IngestTooLarge)
async def _too_large(_: Request, exc: ingest.IngestTooLarge) -> JSONResponse:
    return _error(413, str(exc))


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return _error(500, "internal error", type(exc).__name__)


# -- health and identity ----------------------------------------------------


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, Any]:
    ok = await db.ping()
    return {"ok": ok, "db": ok, "adapters": sorted(ADAPTERS)}


@app.get("/v1/me", response_model=UserOut, tags=["identity"])
async def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email, display_name=user.display_name)


# -- write ------------------------------------------------------------------


@app.post("/v1/ingest", response_model=IngestResult, tags=["ingest"])
async def ingest_canonical(
    batch: IngestBatch,
    user: User = Depends(current_user),
) -> IngestResult:
    """The contract endpoint: any agent that speaks canonical JSON posts here."""
    return await ingest.ingest_batch(user, batch.session, batch.events)


@app.post("/v1/ingest/raw/{agent_kind}", response_model=IngestResult, tags=["ingest"])
async def ingest_raw(
    request: Request,
    agent_kind: str = Path(pattern=r"^[a-z0-9_]+$"),
    user: User = Depends(current_user),
) -> IngestResult:
    """Native NDJSON straight off a laptop. The adapter runs here, not there (D3)."""
    adapter = get_adapter(agent_kind)

    declared = request.headers.get("content-length")
    if declared and int(declared) > SETTINGS.max_body_bytes:
        raise HTTPException(status_code=413, detail=f"body exceeds {SETTINGS.max_body_bytes} bytes")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > SETTINGS.max_body_bytes:
            raise HTTPException(
                status_code=413, detail=f"body exceeds {SETTINGS.max_body_bytes} bytes"
            )

    lines = body.decode("utf-8", errors="replace").splitlines()
    parsed = adapter.parse(lines)
    return await ingest.ingest_batch(user, parsed.session, parsed.events, skipped=parsed.skipped)


# -- read -------------------------------------------------------------------


def _page_limit(limit: int | None) -> int:
    return min(limit or SETTINGS.default_page_limit, SETTINGS.max_page_limit)


@app.get("/v1/sessions", response_model=list[SessionOut], tags=["read"])
async def list_sessions(
    user_id: UUID | None = None,
    agent_kind: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=50, ge=1),
    _: User = Depends(current_user),
) -> list[SessionOut]:
    rows = await db.list_sessions(user_id, agent_kind, since, _page_limit(limit))
    return [SessionOut(**dict(row)) for row in rows]


@app.get("/v1/sessions/{session_id}", response_model=SessionOut, tags=["read"])
async def get_session(session_id: UUID, _: User = Depends(current_user)) -> SessionOut:
    row = await db.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such session")
    return SessionOut(**dict(row))


@app.get("/v1/sessions/{session_id}/events", response_model=list[EventOut], tags=["read"])
async def list_session_events(
    session_id: UUID,
    after_seq: int = Query(default=-1),
    limit: int = Query(default=SETTINGS.default_page_limit, ge=1),
    _: User = Depends(current_user),
) -> list[EventOut]:
    rows = await db.list_session_events(session_id, after_seq, _page_limit(limit))
    return [EventOut(**dict(row)) for row in rows]


@app.get("/v1/events", response_model=list[EventOut], tags=["read"])
async def list_events(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=SETTINGS.default_page_limit, ge=1),
    types: str | None = Query(default=None, description="comma-separated event_type filter"),
    user_id: UUID | None = None,
    _: User = Depends(current_user),
) -> list[EventOut]:
    """The Oracle cursor feed: global insertion order (D6).

    A poller should re-read from a little before its last id, because bigserial
    is assigned before commit and a concurrent write can land out of order.
    """
    type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
    rows = await db.list_events(after_id, _page_limit(limit), type_list, user_id)
    return [EventOut(**dict(row)) for row in rows]


@app.get("/v1/inbox", tags=["read"])
async def inbox(
    session_id: UUID | None = None,
    user: User = Depends(current_user),
) -> list[dict[str, Any]]:
    """Phase 3 delivery channel. The table is live; the Oracle agent fills it."""
    rows = await db.pending_messages(user.id, session_id)
    return [dict(row) for row in rows]
