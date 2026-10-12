"""SQLite run store: schema plus plain repository functions."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ACTIVE_STATUSES = ("queued", "running")
FINAL_STATUSES = ("succeeded", "failed", "timed_out")
STATUSES = ACTIVE_STATUSES + FINAL_STATUSES

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    issue_title TEXT NOT NULL,
    issue_body TEXT NOT NULL DEFAULT '',
    issue_url TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_reason TEXT,
    error_detail TEXT,
    devin_session_id TEXT,
    devin_session_url TEXT,
    devin_status TEXT,
    devin_status_detail TEXT,
    pr_url TEXT,
    pr_state TEXT,
    acus_consumed REAL,
    idle_polls INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    session_started_at TEXT,
    pr_opened_at TEXT,
    finished_at TEXT,
    last_polled_at TEXT
);

-- Makes a second active run for the same issue impossible, even under a race.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_issue
    ON runs (repo, issue_number)
    WHERE status IN ('queued', 'running');
"""

_UPDATABLE = {
    "status", "failure_reason", "error_detail", "devin_session_id",
    "devin_session_url", "devin_status", "devin_status_detail", "pr_url",
    "pr_state", "acus_consumed", "idle_polls", "session_started_at",
    "pr_opened_at", "finished_at", "last_polled_at",
}


class DuplicateActiveRun(Exception):
    """The issue already has a queued or running run."""

    def __init__(self, existing: dict):
        super().__init__(f"run {existing['id']} is already active for this issue")
        self.existing = existing


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def create_run(
    conn: sqlite3.Connection,
    *,
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    issue_url: str,
    trigger: str,
) -> dict:
    try:
        cur = conn.execute(
            "INSERT INTO runs (repo, issue_number, issue_title, issue_body, issue_url,"
            " trigger, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
            (repo, issue_number, issue_title, issue_body, issue_url, trigger, utcnow()),
        )
    except sqlite3.IntegrityError:
        raise DuplicateActiveRun(get_active_run(conn, repo, issue_number)) from None
    return get_run(conn, cur.lastrowid)


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def get_active_run(conn: sqlite3.Connection, repo: str, issue_number: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runs WHERE repo = ? AND issue_number = ?"
        " AND status IN ('queued', 'running')",
        (repo, issue_number),
    ).fetchone()
    return dict(row) if row else None


def has_run(conn: sqlite3.Connection, repo: str, issue_number: int) -> bool:
    """Whether the issue has ever had a run, in any status."""
    row = conn.execute(
        "SELECT 1 FROM runs WHERE repo = ? AND issue_number = ? LIMIT 1", (repo, issue_number)
    ).fetchone()
    return row is not None


def list_runs(
    conn: sqlite3.Connection, *, status: str | None = None, limit: int | None = 50
) -> list[dict]:
    sql, params = "SELECT * FROM runs", []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(row) for row in conn.execute(sql, params)]


def list_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    """Runs in one status, oldest first, so the worker handles them in arrival order."""
    rows = conn.execute("SELECT * FROM runs WHERE status = ? ORDER BY id", (status,))
    return [dict(row) for row in rows]


def update_run(conn: sqlite3.Connection, run_id: int, **fields) -> dict:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE runs SET {assignments} WHERE id = ?", (*fields.values(), run_id))
    return get_run(conn, run_id)
