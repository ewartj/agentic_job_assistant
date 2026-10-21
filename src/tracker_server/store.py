"""SQLite store for job applications, their status history and drafts.

Plain functions over a connection - no MCP here, so the rules (validation, no
duplicates, drafts never overwritten) can be tested directly. server.py is the
thin MCP layer on top.
"""
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, get_args

Status = Literal["interested", "applied", "interviewing", "offer", "rejected", "withdrawn"]
DraftKind = Literal["cover_letter", "motivation", "application_answers"]  # same categories as letters.json
STATUSES: tuple[str, ...] = get_args(Status)
DRAFT_KINDS: tuple[str, ...] = get_args(DraftKind)

# A full date, or just the month or year when that's all that's known (e.g. old applications).
DATE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id         INTEGER PRIMARY KEY,
    company    TEXT NOT NULL,
    role       TEXT NOT NULL,
    posting    TEXT,              -- a saved posting's name (postings://posting/<name>)
    url        TEXT,
    status     TEXT NOT NULL,
    applied_on TEXT,              -- set the first time the status becomes 'applied'
    notes      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- One application per company + role, ignoring case. The database enforces it
    -- even if two writes race; log_application checks first to give a friendly answer.
    UNIQUE (company COLLATE NOCASE, role COLLATE NOCASE)
);

-- Every status change is kept, so update_status never loses information.
CREATE TABLE IF NOT EXISTS status_history (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    status         TEXT NOT NULL,
    note           TEXT,
    changed_at     TEXT NOT NULL
);

-- Drafts are versioned: saving again adds version n+1 rather than overwriting.
CREATE TABLE IF NOT EXISTS drafts (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    kind           TEXT NOT NULL,
    version        INTEGER NOT NULL,
    text           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (application_id, kind, version)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # off by default in SQLite
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean(value: str | None, field: str, required: bool = False) -> str | None:
    """Trim and collapse whitespace, so "Acme  Health " and "Acme Health" are the same company."""
    cleaned = " ".join(value.split()) if value else None
    if required and not cleaned:
        raise ValueError(f"{field} is required")
    return cleaned


def _check(value: str, allowed: tuple[str, ...], field: str) -> None:
    if value not in allowed:
        raise ValueError(f"Invalid {field} {value!r}: use one of {', '.join(allowed)}")


def _require_application(conn: sqlite3.Connection, application_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    if row is None:
        raise ValueError(f"No application with id {application_id} - call list_applications to see the ids")
    return row


def log_application(
    conn: sqlite3.Connection,
    company: str,
    role: str,
    posting: str | None = None,
    url: str | None = None,
    status: str = "interested",
    notes: str | None = None,
    applied_on: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """(application, created). Logging the same company + role again returns the
    existing application unchanged, with created=False - so retries are safe.

    applied_on defaults to today when status is "applied"; pass it for past applications."""
    company, role = _clean(company, "company", required=True), _clean(role, "role", required=True)
    _check(status, STATUSES, "status")
    if applied_on is not None and not DATE.fullmatch(applied_on):
        raise ValueError(f"Invalid applied_on {applied_on!r}: use YYYY-MM-DD (or YYYY-MM, or YYYY)")
    existing = conn.execute(
        "SELECT id FROM applications WHERE company = ? COLLATE NOCASE AND role = ? COLLATE NOCASE",
        (company, role),
    ).fetchone()
    if existing:
        return get_application(conn, existing["id"]), False

    now = _now()
    with conn:  # one transaction: the application and its first history row, or neither
        cur = conn.execute(
            "INSERT INTO applications (company, role, posting, url, status, applied_on, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (company, role, _clean(posting, "posting"), _clean(url, "url"), status,
             applied_on or (date.today().isoformat() if status == "applied" else None), notes, now, now),
        )
        conn.execute(
            "INSERT INTO status_history (application_id, status, note, changed_at) VALUES (?, ?, ?, ?)",
            (cur.lastrowid, status, "logged", now),
        )
    return get_application(conn, cur.lastrowid), True


def update_status(
    conn: sqlite3.Connection, application_id: int, status: str, note: str | None = None
) -> dict[str, Any]:
    """Change an application's status and record it in its history. Setting the
    status it already has changes nothing, so retries are safe."""
    _check(status, STATUSES, "status")
    current = _require_application(conn, application_id)
    if current["status"] == status:
        return get_application(conn, application_id)

    now = _now()
    with conn:
        conn.execute(
            "UPDATE applications SET status = ?, updated_at = ?,"
            " applied_on = COALESCE(applied_on, CASE WHEN ? = 'applied' THEN ? END) WHERE id = ?",
            (status, now, status, date.today().isoformat(), application_id),
        )
        conn.execute(
            "INSERT INTO status_history (application_id, status, note, changed_at) VALUES (?, ?, ?, ?)",
            (application_id, status, note, now),
        )
    return get_application(conn, application_id)


def list_applications(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    if status is not None:
        _check(status, STATUSES, "status")
    rows = conn.execute(
        "SELECT * FROM applications WHERE ? IS NULL OR status = ? ORDER BY updated_at DESC, id DESC",
        (status, status),
    ).fetchall()
    return [dict(row) for row in rows]


def get_application(conn: sqlite3.Connection, application_id: int) -> dict[str, Any]:
    """The application, its status history, and its drafts (without their text)."""
    application = dict(_require_application(conn, application_id))
    application["history"] = [dict(r) for r in conn.execute(
        "SELECT status, note, changed_at FROM status_history WHERE application_id = ? ORDER BY id",
        (application_id,),
    )]
    application["drafts"] = [dict(r) for r in conn.execute(
        "SELECT id, kind, version, length(text) AS characters, created_at FROM drafts"
        " WHERE application_id = ? ORDER BY kind, version",
        (application_id,),
    )]
    return application


def save_draft(conn: sqlite3.Connection, application_id: int, kind: str, text: str) -> dict[str, Any]:
    """Save a new version of a draft. Earlier versions are kept, never overwritten."""
    _check(kind, DRAFT_KINDS, "kind")
    if not text.strip():
        raise ValueError("draft text is empty")
    _require_application(conn, application_id)
    with conn:
        (version,) = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM drafts WHERE application_id = ? AND kind = ?",
            (application_id, kind),
        ).fetchone()
        cur = conn.execute(
            "INSERT INTO drafts (application_id, kind, version, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (application_id, kind, version, text, _now()),
        )
    return {"id": cur.lastrowid, "application_id": application_id, "kind": kind, "version": version}


def get_draft(conn: sqlite3.Connection, application_id: int, kind: str, version: int | None = None) -> dict[str, Any]:
    """A draft's text: the latest version unless one is given."""
    _check(kind, DRAFT_KINDS, "kind")
    _require_application(conn, application_id)
    row = conn.execute(
        "SELECT * FROM drafts WHERE application_id = ? AND kind = ? AND (? IS NULL OR version = ?)"
        " ORDER BY version DESC LIMIT 1",
        (application_id, kind, version, version),
    ).fetchone()
    if row is None:
        which = f"version {version} of " if version else ""
        raise ValueError(f"No {which}{kind} draft for application {application_id}")
    return dict(row)
