"""Transactional SQLite state, history, and replayable delivery."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .protocol import now


def dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS permissions(project_id TEXT, human_id TEXT, role TEXT,
 PRIMARY KEY(project_id,human_id));
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, project_id TEXT, human_id TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS sessions_project ON sessions(project_id);
CREATE TABLE IF NOT EXISTS registrations(project_id TEXT, human_id TEXT, machine_id TEXT,
 agent_session_id TEXT, session_id TEXT, PRIMARY KEY(project_id,human_id,machine_id,agent_session_id));
CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY, project_id TEXT, subject TEXT, predicate TEXT,
 lifecycle TEXT, session_id TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS facts_subject ON facts(subject,predicate,lifecycle);
CREATE TABLE IF NOT EXISTS entities(project_id TEXT, name TEXT, type TEXT, data TEXT,
 PRIMARY KEY(project_id,name));
CREATE TABLE IF NOT EXISTS relationships(project_id TEXT, subject TEXT, predicate TEXT, object TEXT,
 source_id TEXT, PRIMARY KEY(project_id,subject,predicate,object));
CREATE INDEX IF NOT EXISTS relationship_object ON relationships(object,predicate);
CREATE TABLE IF NOT EXISTS shares(provider TEXT, consumer TEXT, subject TEXT,
 PRIMARY KEY(provider,consumer,subject));
CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY, project_id TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS conflicts(id TEXT PRIMARY KEY, project_id TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, project_id TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, session_id TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS events(message_id TEXT PRIMARY KEY, principal TEXT, request_hash TEXT,
 data TEXT, response TEXT, received_at TEXT);
CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
 session_id TEXT, data TEXT, acknowledged_at TEXT);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id,seq);
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT,
 action TEXT, object_id TEXT, data TEXT, timestamp TEXT);
CREATE TABLE IF NOT EXISTS reasoning_jobs(id TEXT PRIMARY KEY, session_id TEXT, subject TEXT,
 state TEXT, attempts INTEGER DEFAULT 0, lease_until TEXT, data TEXT, result TEXT);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            parent = Path(path).expanduser().parent
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript(SCHEMA)
        if self.path != ":memory:":
            os.chmod(self.path, 0o600)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def one(self, sql: str, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def rows(self, sql: str, args=()):
        return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def record(self, table: str, record_id: str):
        if table not in {"projects", "sessions", "facts", "questions", "conflicts", "decisions"}:
            raise ValueError("unknown record table")
        row = self.one(f"SELECT data FROM {table} WHERE id=?", (record_id,))
        return json.loads(row["data"]) if row else None

    def records(self, table: str, project_id: str | None = None):
        if table not in {"sessions", "facts", "questions", "conflicts", "decisions"}:
            raise ValueError("unknown record table")
        where, args = (" WHERE project_id=?", (project_id,)) if project_id else ("", ())
        return [json.loads(row["data"]) for row in self.rows(f"SELECT data FROM {table}{where}", args)]

    def save(self, table: str, record):
        value = record.model_dump(mode="json") if hasattr(record, "model_dump") else record
        keys = {
            "sessions": "session_id",
            "facts": "fact_id",
            "questions": "question_id",
            "conflicts": "conflict_id",
            "decisions": "decision_id",
        }
        if table not in keys:
            raise ValueError("unknown record table")
        columns = {"id": value[keys[table]], "project_id": value["project_id"], "data": dump(value)}
        if table == "sessions":
            columns["human_id"] = value["human_id"]
        if table == "facts":
            columns.update({key: value[key] for key in ["subject", "predicate", "lifecycle", "session_id"]})
        names = list(columns)
        self.db.execute(
            f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' for _ in names)}) "
            f"ON CONFLICT(id) DO UPDATE SET {','.join(n + '=excluded.' + n for n in names if n != 'id')}",
            tuple(columns.values()),
        )

    def audit(self, actor: str, action: str, object_id: str, data: Any):
        self.db.execute(
            "INSERT INTO audit(actor,action,object_id,data,timestamp) VALUES(?,?,?,?,?)",
            (actor, action, object_id, dump(data), now()),
        )

    def deliver(self, command):
        self.db.execute(
            "INSERT INTO messages(id,session_id,data) VALUES(?,?,?)",
            (command.command_id, command.session_id, dump(command)),
        )
        self.audit(
            "oracle",
            "delivery_queued",
            command.command_id,
            {"session_id": command.session_id, "type": command.type},
        )

    def active_facts(self, subject: str, predicate: str | None = None):
        query = "SELECT data FROM facts WHERE subject=? AND lifecycle IN ('ACTIVE','DISPUTED')"
        args = [subject]
        if predicate is not None:
            query += " AND predicate=?"
            args.append(predicate)
        return [json.loads(r["data"]) for r in self.rows(query, args)]
