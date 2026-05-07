"""SQLite state for lc-coach.

One DB file, one user. Default location is `~/.lc-coach/state.db`; override with
the `LC_COACH_DB` env var. All schema is created idempotently by `init_db()`.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_DB_PATH = Path.home() / ".lc-coach" / "state.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    difficulty TEXT,
    tags_json TEXT,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_slug TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT,
    code_snapshot TEXT,
    language TEXT,
    time_spent_seconds INTEGER,
    FOREIGN KEY (problem_slug) REFERENCES problems(slug)
);

CREATE INDEX IF NOT EXISTS idx_attempts_slug_active
    ON attempts(problem_slug, ended_at);

CREATE TABLE IF NOT EXISTS hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_slug TEXT NOT NULL,
    attempt_id INTEGER,
    level INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (problem_slug) REFERENCES problems(slug),
    FOREIGN KEY (attempt_id) REFERENCES attempts(id)
);

CREATE INDEX IF NOT EXISTS idx_hints_slug ON hints(problem_slug);
CREATE INDEX IF NOT EXISTS idx_hints_attempt ON hints(attempt_id);
"""

VALID_OUTCOMES = ("solved", "partial", "stuck")


def db_path() -> Path:
    override = os.environ.get("LC_COACH_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def init_db(path: Optional[Path] = None) -> Path:
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as conn:
        conn.executescript(SCHEMA)
    return target


@contextmanager
def connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    target = Path(path) if path else db_path()
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_problem(
    conn: sqlite3.Connection,
    *,
    slug: str,
    title: str,
    statement: str,
    difficulty: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> None:
    """Idempotent upsert. Updates title/statement/difficulty/tags on conflict;
    leaves first_seen untouched so we keep the original timestamp."""
    conn.execute(
        """
        INSERT INTO problems (slug, title, statement, difficulty, tags_json, first_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            title = excluded.title,
            statement = excluded.statement,
            difficulty = excluded.difficulty,
            tags_json = excluded.tags_json
        """,
        (
            slug,
            title,
            statement,
            difficulty,
            json.dumps(tags) if tags is not None else None,
            _now_iso(),
        ),
    )


def get_problem(conn: sqlite3.Connection, slug: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM problems WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["tags"] = json.loads(out.pop("tags_json")) if out.get("tags_json") else []
    return out


def record_hint(
    conn: sqlite3.Connection,
    *,
    problem_slug: str,
    level: int,
    prompt: str,
    response: str,
    attempt_id: Optional[int] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO hints (problem_slug, attempt_id, level, prompt, response, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (problem_slug, attempt_id, level, prompt, response, _now_iso()),
    )
    return int(cur.lastrowid)


def get_recent_hints(
    conn: sqlite3.Connection, problem_slug: str, limit: int = 5
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, level, prompt, response, created_at
        FROM hints
        WHERE problem_slug = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (problem_slug, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Attempts -------------------------------------------------------------


def get_active_attempt(
    conn: sqlite3.Connection, problem_slug: str
) -> Optional[dict]:
    """Most recent unfinished attempt for a slug, or None."""
    row = conn.execute(
        """
        SELECT * FROM attempts
        WHERE problem_slug = ? AND ended_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (problem_slug,),
    ).fetchone()
    return dict(row) if row else None


def start_attempt(conn: sqlite3.Connection, *, problem_slug: str) -> dict:
    """Idempotent: if there's already an active attempt for this slug, return
    it. Otherwise insert a new one and return it."""
    existing = get_active_attempt(conn, problem_slug)
    if existing is not None:
        return existing
    cur = conn.execute(
        "INSERT INTO attempts (problem_slug, started_at) VALUES (?, ?)",
        (problem_slug, _now_iso()),
    )
    attempt_id = int(cur.lastrowid)
    row = conn.execute(
        "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    return dict(row)


def finish_attempt(
    conn: sqlite3.Connection,
    *,
    attempt_id: int,
    outcome: str,
    code_snapshot: Optional[str],
    language: Optional[str] = None,
) -> dict:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(
            f"outcome must be one of {VALID_OUTCOMES} (got {outcome!r})"
        )
    row = conn.execute(
        "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"attempt {attempt_id} not found")
    if row["ended_at"] is not None:
        raise ValueError(f"attempt {attempt_id} already finished")

    started_at = datetime.fromisoformat(row["started_at"])
    ended_at = datetime.now(timezone.utc)
    duration = int((ended_at - started_at).total_seconds())

    conn.execute(
        """
        UPDATE attempts
        SET ended_at = ?, outcome = ?, code_snapshot = ?, language = ?,
            time_spent_seconds = ?
        WHERE id = ?
        """,
        (
            ended_at.isoformat(timespec="seconds"),
            outcome,
            code_snapshot,
            language,
            duration,
            attempt_id,
        ),
    )
    finished = conn.execute(
        "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    return dict(finished)


def get_attempt(conn: sqlite3.Connection, attempt_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    return dict(row) if row else None
