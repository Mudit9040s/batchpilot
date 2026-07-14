"""SQLite job history. Zero external dependencies."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

DATA_DIR = Path(os.environ.get("BATCHPILOT_DATA_DIR", "data"))
DB_PATH = DATA_DIR / "batchpilot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    profile_key TEXT NOT NULL,
    filename TEXT NOT NULL,
    total_rows INTEGER NOT NULL,
    error_rows INTEGER DEFAULT 0,
    warning_rows INTEGER DEFAULT 0,
    ai_used INTEGER DEFAULT 0,
    status TEXT DEFAULT 'validated',  -- validated | sent
    success_rows INTEGER,
    failed_rows INTEGER,
    payload_json TEXT,   -- headers, rows, issues, outcomes
    report_path TEXT
);
"""


_init_lock = threading.Lock()
_initialized_for: str | None = None


def _conn() -> sqlite3.Connection:
    global _initialized_for
    # One-time init under a lock: WAL mode is persistent (stored in the DB
    # file), and switching modes concurrently on a fresh DB races. WAL lets
    # many readers + one writer work simultaneously without lock errors.
    if _initialized_for != str(DB_PATH):
        with _init_lock:
            if _initialized_for != str(DB_PATH):
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                boot = sqlite3.connect(DB_PATH, timeout=30)
                boot.execute("PRAGMA journal_mode=WAL")
                boot.execute(SCHEMA)
                boot.commit()
                boot.close()
                _initialized_for = str(DB_PATH)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def claim_send(job_id: str) -> bool:
    """Atomically claim a job for sending. Returns False if someone else
    (or a double-click) already sent / is sending it."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE jobs SET status='sending' WHERE id=? AND status='validated'",
            (job_id,))
    return cur.rowcount == 1


def create_job(profile_key: str, filename: str, headers: list, rows: list,
               issues: list, ai_used: bool, profile_dict: dict | None = None,
               mode: str = "profile") -> str:
    job_id = uuid.uuid4().hex[:12]
    err = sum(1 for lst in issues if any(i["severity"] == "error" for i in lst))
    warn = sum(1 for lst in issues if lst and not any(i["severity"] == "error" for i in lst))
    payload = json.dumps({"headers": headers, "rows": rows, "issues": issues,
                          "outcomes": None, "profile": profile_dict,
                          "mode": mode}, default=str)
    with _conn() as c:
        c.execute(
            "INSERT INTO jobs (id, created_at, profile_key, filename, total_rows,"
            " error_rows, warning_rows, ai_used, payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, time.time(), profile_key, filename, len(rows), err, warn,
             int(ai_used), payload))
    return job_id


def get_job(job_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json") or "{}")
    return d


def mark_sent(job_id: str, outcomes: list, report_path: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    payload = job["payload"]
    payload["outcomes"] = outcomes
    ok = sum(1 for o in outcomes if o["status"] == "success")
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET status='sent', success_rows=?, failed_rows=?,"
            " payload_json=?, report_path=? WHERE id=?",
            (ok, len(outcomes) - ok, json.dumps(payload, default=str),
             report_path, job_id))


def list_jobs(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, created_at, profile_key, filename, total_rows, error_rows,"
            " warning_rows, ai_used, status, success_rows, failed_rows"
            " FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
