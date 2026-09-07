from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATES = ("QUEUED", "IDENTIFYING", "AUDITING", "WAITING_FOR_IRD", "RECONSTRUCTING", "BUILDING_ISO", "VERIFYING", "PUBLISHING", "WAITING_FOR_PS3_IDLE", "READY", "FAILED", "NEEDS_ATTENTION")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
              id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, fingerprint TEXT,
              title TEXT, title_id TEXT, source_type TEXT, state TEXT NOT NULL,
              stage TEXT, reason TEXT, audit_path TEXT, iso_path TEXT, iso_sha256 TEXT,
              repairs INTEGER DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(source, fingerprint)
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT, message TEXT, created_at TEXT NOT NULL);
            """)

    def add_job(self, source: str, fingerprint: str, source_type: str) -> int | None:
        stamp = now()
        with self.connect() as conn:
            cur = conn.execute("INSERT OR IGNORE INTO jobs(source,fingerprint,source_type,state,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (source, fingerprint, source_type, "QUEUED", "queued", stamp, stamp))
            return cur.lastrowid or None

    def update_job(self, job_id: int, **values: Any) -> None:
        values["updated_at"] = now()
        cols = ", ".join(f"{key}=?" for key in values)
        with self.connect() as conn:
            conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*values.values(), job_id))

    def jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {row["state"]: row["count"] for row in conn.execute("SELECT state, COUNT(*) count FROM jobs GROUP BY state")}

    def log(self, level: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO events(level,message,created_at) VALUES(?,?,?)", (level, message, now()))

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
