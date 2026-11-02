"""Transactional durable state and an append-only operational audit."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from scdeploy.security import redact


def now() -> str:
    """Return an unambiguous UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class State:
    """Store history independently from retained application release files."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, started TEXT NOT NULL, ended TEXT,
                    data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS operations_project ON operations(project, started);
            """)

    def connect(self) -> sqlite3.Connection:
        """Use a fresh connection per transaction and wait for short writers."""
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def project(self, name: str) -> dict:
        """Get installed metadata, never deriving it from a Git working tree."""
        with closing(self.connect()) as db:
            row = db.execute("SELECT data FROM projects WHERE id=?", (name,)).fetchone()
        return json.loads(row[0]) if row else {}

    def update(self, name: str, **values: object) -> None:
        """Merge project state in a single immediate transaction."""
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM projects WHERE id=?", (name,)).fetchone()
            data = json.loads(row[0]) if row else {}
            data.update(values)
            db.execute("INSERT OR REPLACE INTO projects VALUES (?, ?)", (name, json.dumps(data)))

    def replace(self, name: str, data: dict) -> None:
        """Restore an exact snapshot, including clearing first-install metadata."""
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO projects VALUES (?, ?)", (name, json.dumps(data)))

    def begin(self, project: str, kind: str, **data: object) -> str:
        """Persist recovery information before a side effect occurs."""
        operation = uuid.uuid4().hex
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?, NULL, ?)",
                       (operation, project, kind, "running", now(), redact(json.dumps(data))))
        return operation

    def finish(self, operation: str, status: str, **data: object) -> None:
        """Complete a journal entry after recovery or successful installation."""
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT data FROM operations WHERE id=?", (operation,)).fetchone()
            values = json.loads(row[0])
            values.update(data)
            db.execute("UPDATE operations SET status=?, ended=?, data=? WHERE id=?",
                       (status, now(), redact(json.dumps(values)), operation))

    def history(self, project: str, pending: bool = False) -> list[dict]:
        """Return chronological history with decoded operation metadata."""
        with closing(self.connect()) as db:
            query = "SELECT * FROM operations WHERE project=?"
            if pending:
                query += " AND status IN ('running', 'rollback_failed')"
            rows = db.execute(query + " ORDER BY started", (project,)).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]
