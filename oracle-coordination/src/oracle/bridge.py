"""Durable local outbox/inbox and cooperative safe-checkpoint control."""

import asyncio
import json
import os
import sqlite3
from pathlib import Path

from .observer import compile_context
from .policy import OracleError
from .protocol import Envelope, EventType, Registration, now
from .store import dump
from .transport import TransportUnavailable


class Bridge:
    def __init__(self, path, transport, principal, project, repository="."):
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS outbox(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
          event TEXT, state TEXT DEFAULT 'PENDING', response TEXT);
        CREATE TABLE IF NOT EXISTS inbox(cursor INTEGER PRIMARY KEY, data TEXT, presented INTEGER DEFAULT 0);
        """)
        os.chmod(path, 0o600)
        self.transport, self.principal, self.project, self.repository = (
            transport,
            principal,
            project,
            repository,
        )
        identity = dump(
            {"principal": principal, "project": project, "repository": str(Path(repository).resolve())}
        )
        if self.get("identity") and self.get("identity") != identity:
            raise OracleError("BRIDGE_IDENTITY", "Use a separate bridge database for each session identity")
        self.set("identity", identity)
        self.lock = asyncio.Lock()

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key, value):
        self.db.execute(
            "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.db.commit()

    @property
    def session_id(self):
        return self.get("session_id")

    async def register(self, registration: Registration):
        if registration.human_id != self.principal or registration.project_id != self.project:
            raise OracleError("BRIDGE_IDENTITY", "Registration does not match bridge identity")
        if self.session_id:
            return {"oracle_session_id": self.session_id, "status": "registered"}
        result = await self.emit(EventType.SESSION_REGISTER, registration.model_dump(mode="json"))
        if "oracle_session_id" in result:
            self.set("session_id", result["oracle_session_id"])
        return result

    async def emit(self, kind, payload):
        if kind != EventType.SESSION_REGISTER and not self.session_id:
            raise OracleError("NOT_REGISTERED", "Register this bridge before reporting work")
        event = Envelope(
            sender=self.principal,
            project_id=self.project,
            session_id=self.session_id,
            type=kind,
            payload=payload,
        )
        self.db.execute("INSERT INTO outbox(id,event) VALUES(?,?)", (event.message_id, dump(event)))
        self.db.commit()
        await self.flush()
        row = self.db.execute("SELECT state,response FROM outbox WHERE id=?", (event.message_id,)).fetchone()
        if row["state"] == "SENT":
            return json.loads(row["response"])
        if row["state"] == "FAILED":
            error = json.loads(row["response"])
            raise OracleError(error["code"], error["message"])
        return {"status": "QUEUED_OFFLINE", "message_id": event.message_id}

    async def flush(self):
        async with self.lock:
            for row in self.db.execute("SELECT * FROM outbox WHERE state='PENDING' ORDER BY seq").fetchall():
                event = json.loads(row["event"])
                try:
                    result = await self.transport.send({"op": "ingest", "event": event})
                except TransportUnavailable:
                    self.set("connectivity", "OFFLINE")
                    return
                except OracleError as error:
                    self.db.execute(
                        "UPDATE outbox SET state='FAILED',response=? WHERE id=?",
                        (dump({"code": error.code, "message": str(error)}), row["id"]),
                    )
                    self.db.commit()
                    continue
                self.db.execute(
                    "UPDATE outbox SET state='SENT',response=? WHERE id=?", (dump(result), row["id"])
                )
                self.db.commit()
                if "oracle_session_id" in result:
                    self.set("session_id", result["oracle_session_id"])
                self.set("connectivity", "ONLINE")

    async def poll(self, *, presented=True):
        if not self.session_id:
            return {"status": "NOT_REGISTERED", "messages": []}
        try:
            result = await self.transport.receive(self.session_id, int(self.get("cursor", "0")))
            with self.db:
                for message in result["messages"]:
                    self.db.execute(
                        "INSERT OR IGNORE INTO inbox(cursor,data) VALUES(?,?)",
                        (message["cursor"], dump(message)),
                    )
                self.db.execute(
                    "INSERT INTO meta VALUES('cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(result["next_cursor"]),),
                )
            await self.transport.send(
                {"op": "ack_delivery", "session_id": self.session_id, "cursor": result["next_cursor"]}
            )
            self.set("connectivity", "ONLINE")
        except TransportUnavailable:
            self.set("connectivity", "OFFLINE")
        # Process the complete persisted inbox in order; an older RESUME cannot override a later PAUSE.
        state = "RUNNING"
        for row in self.db.execute("SELECT data FROM inbox ORDER BY cursor"):
            kind = json.loads(row["data"])["type"]
            if kind == EventType.PAUSE:
                state = "PAUSE_REQUESTED"
            elif kind == EventType.FINAL_DECISION:
                state = "RESOLUTION_PENDING"
            elif kind == EventType.RESUME:
                state = "RUNNING"
        self.set("work_state", state)
        rows = self.db.execute("SELECT cursor,data FROM inbox WHERE presented=0 ORDER BY cursor").fetchall()
        if presented:
            with self.db:
                self.db.executemany(
                    "UPDATE inbox SET presented=1 WHERE cursor=?", [(r["cursor"],) for r in rows]
                )
        return {
            "status": state,
            "connectivity": self.get("connectivity", "UNKNOWN"),
            "messages": [json.loads(r["data"]) for r in rows],
        }

    async def context(self, subjects=None):
        return await self.transport.send(
            {"op": "context", "session_id": self.session_id, "subjects": subjects}
        )

    async def checkpoint(self, packet=None):
        packet = packet or compile_context(self.session_id, self.repository)
        result = await self.emit(EventType.CHECKPOINT, packet.model_dump(mode="json"))
        return {"checkpoint": result, "updates": await self.poll()}

    async def gate(self, action, target, proposed_change, revision=None):
        # Gates are never put in the offline queue: authorization is time- and revision-sensitive.
        if not self.session_id:
            return {"status": "UNAVAILABLE", "allow": False, "reason": "Bridge is not registered"}
        await self.flush()
        event = Envelope(
            sender=self.principal,
            project_id=self.project,
            session_id=self.session_id,
            type=EventType.GATE_REQUEST,
            payload={
                "action": action,
                "target": target,
                "proposed_change": proposed_change,
                "revision": revision,
            },
        )
        try:
            return await self.transport.send({"op": "ingest", "event": event.model_dump(mode="json")})
        except TransportUnavailable:
            return {
                "status": "UNAVAILABLE",
                "allow": False,
                "reason": "Oracle unavailable; shared-contract changes are blocked",
            }

    def status(self):
        return {
            "session_id": self.session_id,
            "project_id": self.project,
            "connectivity": self.get("connectivity", "UNKNOWN"),
            "work_state": self.get("work_state", "RUNNING"),
            "queued": self.db.execute("SELECT count(*) FROM outbox WHERE state='PENDING'").fetchone()[0],
            "failed": self.db.execute("SELECT count(*) FROM outbox WHERE state='FAILED'").fetchone()[0],
            "last_heartbeat": self.get("last_heartbeat"),
        }

    async def run(self, heartbeat_seconds=60):
        while True:
            await self.flush()
            if self.session_id:
                await self.emit(EventType.HEARTBEAT, {})
                await self.poll(presented=False)
                self.set("last_heartbeat", now())
            await asyncio.sleep(heartbeat_seconds)
