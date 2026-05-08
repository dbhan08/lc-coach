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

CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS problem_patterns (
    problem_slug TEXT NOT NULL,
    pattern_id INTEGER NOT NULL,
    PRIMARY KEY (problem_slug, pattern_id),
    FOREIGN KEY (problem_slug) REFERENCES problems(slug),
    FOREIGN KEY (pattern_id) REFERENCES patterns(id)
);

CREATE TABLE IF NOT EXISTS mastery (
    pattern_id INTEGER PRIMARY KEY,
    elo REAL NOT NULL,
    n_attempts INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT,
    FOREIGN KEY (pattern_id) REFERENCES patterns(id)
);

CREATE TABLE IF NOT EXISTS companies (
    name TEXT PRIMARY KEY,
    last_ingested_at TEXT
);

CREATE TABLE IF NOT EXISTS company_problems (
    company TEXT NOT NULL,
    problem_slug TEXT NOT NULL,
    leetcode_id INTEGER,
    title TEXT,
    difficulty TEXT,
    appearances_json TEXT,
    confidence REAL NOT NULL,
    PRIMARY KEY (company, problem_slug),
    FOREIGN KEY (company) REFERENCES companies(name)
);
CREATE INDEX IF NOT EXISTS idx_company_problems_company ON company_problems(company);
CREATE INDEX IF NOT EXISTS idx_company_problems_slug ON company_problems(problem_slug);

CREATE TABLE IF NOT EXISTS reviews (
    problem_slug TEXT PRIMARY KEY,
    ease REAL NOT NULL DEFAULT 2.5,
    repetitions INTEGER NOT NULL DEFAULT 0,
    interval_days INTEGER NOT NULL DEFAULT 0,
    due_date TEXT,
    last_quality INTEGER,
    last_reviewed_at TEXT,
    FOREIGN KEY (problem_slug) REFERENCES problems(slug)
);
CREATE INDEX IF NOT EXISTS idx_reviews_due ON reviews(due_date);

CREATE TABLE IF NOT EXISTS premium_slugs (
    slug TEXT PRIMARY KEY,
    marked_at TEXT NOT NULL
);
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
        # Bootstrap the coarse-pattern taxonomy idempotently. Imported lazily
        # to avoid a circular import at module load.
        from lc_coach.mastery import COARSE_PATTERNS

        conn.executemany(
            "INSERT OR IGNORE INTO patterns (name) VALUES (?)",
            [(p,) for p in COARSE_PATTERNS],
        )
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


# --- Patterns + mastery ---------------------------------------------------


def _pattern_id(conn: sqlite3.Connection, name: str) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM patterns WHERE name = ?", (name,)
    ).fetchone()
    return int(row[0]) if row else None


def assign_patterns_to_problem(
    conn: sqlite3.Connection,
    *,
    slug: str,
    pattern_names: list[str],
) -> list[str]:
    """Idempotently link a problem to its coarse patterns. Unknown pattern
    names are dropped silently. Returns the patterns that were attached."""
    attached: list[str] = []
    for name in pattern_names:
        pid = _pattern_id(conn, name)
        if pid is None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO problem_patterns (problem_slug, pattern_id) "
            "VALUES (?, ?)",
            (slug, pid),
        )
        attached.append(name)
    return attached


def get_problem_patterns(conn: sqlite3.Connection, slug: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.id, p.name FROM patterns p
        JOIN problem_patterns pp ON pp.pattern_id = p.id
        WHERE pp.problem_slug = ?
        """,
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]


def _max_hint_level_for_attempt(
    conn: sqlite3.Connection, attempt_id: int
) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(level), 0) FROM hints WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def update_mastery_for_attempt(
    conn: sqlite3.Connection, attempt_id: int
) -> list[dict]:
    """Recompute pattern Elos for every pattern this attempt's problem is
    tagged with. Returns one update record per pattern (for the response).

    Idempotency note: this is meant to be called once per finished attempt.
    Calling twice would double-count; the app layer is responsible for
    invoking it at /attempts/done time only.
    """
    from lc_coach.mastery import (
        INITIAL_PATTERN_ELO,
        attempt_score,
        elo_update,
        problem_effective_elo,
    )

    attempt = conn.execute(
        "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    if attempt is None:
        return []
    if attempt["outcome"] is None:
        # not finished yet — nothing to do
        return []

    problem = conn.execute(
        "SELECT * FROM problems WHERE slug = ?",
        (attempt["problem_slug"],),
    ).fetchone()
    if problem is None:
        return []

    patterns = get_problem_patterns(conn, attempt["problem_slug"])
    if not patterns:
        return []

    p_elo = problem_effective_elo(problem["difficulty"])
    max_level = _max_hint_level_for_attempt(conn, attempt_id)
    score = attempt_score(attempt["outcome"], max_level)

    out: list[dict] = []
    for pat in patterns:
        existing = conn.execute(
            "SELECT elo, n_attempts FROM mastery WHERE pattern_id = ?",
            (pat["id"],),
        ).fetchone()
        if existing is None:
            old_elo = INITIAL_PATTERN_ELO
            n_attempts = 0
        else:
            old_elo = float(existing["elo"])
            n_attempts = int(existing["n_attempts"])

        new_elo = elo_update(old_elo, p_elo, score)
        new_n = n_attempts + 1

        conn.execute(
            """
            INSERT INTO mastery (pattern_id, elo, n_attempts, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(pattern_id) DO UPDATE SET
                elo = excluded.elo,
                n_attempts = excluded.n_attempts,
                last_updated = excluded.last_updated
            """,
            (pat["id"], new_elo, new_n, _now_iso()),
        )
        out.append(
            {
                "pattern_id": pat["id"],
                "pattern_name": pat["name"],
                "old_elo": old_elo,
                "new_elo": new_elo,
                "delta": new_elo - old_elo,
                "n_attempts": new_n,
                "score": score,
            }
        )
    return out


def get_weakest_patterns(
    conn: sqlite3.Connection, n: int = 5, *, attempted_only: bool = True
) -> list[dict]:
    """Return up to n patterns sorted by Elo ascending. By default only
    includes patterns the user has attempted (otherwise the list is just
    'every untouched pattern is at 1200', which is noise)."""
    where = "WHERE m.n_attempts > 0" if attempted_only else ""
    rows = conn.execute(
        f"""
        SELECT p.id, p.name, m.elo, m.n_attempts, m.last_updated
        FROM patterns p
        LEFT JOIN mastery m ON m.pattern_id = p.id
        {where}
        ORDER BY COALESCE(m.elo, ?) ASC
        LIMIT ?
        """,
        (1200.0, n),
    ).fetchall()
    return [dict(r) for r in rows]


def get_full_mastery(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.id, p.name,
               COALESCE(m.elo, 1200.0) AS elo,
               COALESCE(m.n_attempts, 0) AS n_attempts,
               m.last_updated
        FROM patterns p
        LEFT JOIN mastery m ON m.pattern_id = p.id
        ORDER BY p.name
        """,
    ).fetchall()
    return [dict(r) for r in rows]


# --- Companies + ingest ---------------------------------------------------


def store_aggregated_companies(
    conn: sqlite3.Connection, aggregated: dict
) -> dict:
    """Persist the output of `ingest.aggregate()`. Returns counts.

    For each ingested (company, slug) row, we ALSO upsert a stub into the
    `problems` table (so problem_patterns FK works) and assign heuristically-
    inferred coarse patterns. This means skill/improve mode can find problems
    even before the user has opened them — the user's lazy pattern tagging on
    `/problems` later will overwrite/augment these heuristic tags.
    """
    from lc_coach.mastery import infer_patterns_from_slug

    now = _now_iso()
    n_companies = 0
    n_problems = 0
    n_problem_stubs = 0
    n_pattern_assignments = 0
    for company, rows in aggregated.items():
        if not rows:
            continue
        conn.execute(
            """
            INSERT INTO companies (name, last_ingested_at) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET last_ingested_at = excluded.last_ingested_at
            """,
            (company, now),
        )
        n_companies += 1
        for row in rows.values():
            appearances = sorted(list(row.appearances))
            conn.execute(
                """
                INSERT INTO company_problems
                  (company, problem_slug, leetcode_id, title, difficulty,
                   appearances_json, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company, problem_slug) DO UPDATE SET
                  leetcode_id = COALESCE(excluded.leetcode_id, company_problems.leetcode_id),
                  title = CASE WHEN length(excluded.title) > 0 THEN excluded.title ELSE company_problems.title END,
                  difficulty = COALESCE(excluded.difficulty, company_problems.difficulty),
                  appearances_json = excluded.appearances_json,
                  confidence = excluded.confidence
                """,
                (
                    company,
                    row.slug,
                    row.leetcode_id,
                    row.title,
                    row.difficulty,
                    json.dumps([list(t) for t in appearances]),
                    float(row.confidence),
                ),
            )
            n_problems += 1

            # Upsert stub problems row so problem_patterns FK is satisfied.
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO problems
                    (slug, title, statement, difficulty, tags_json, first_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row.slug,
                    row.title or row.slug,
                    "(ingested stub — full statement loads when you open the problem)",
                    row.difficulty,
                    None,
                    now,
                ),
            )
            if cur.rowcount > 0:
                n_problem_stubs += 1

            # Heuristic pattern tags. Use INSERT OR IGNORE so we don't clobber
            # any patterns the user has already attached via /problems.
            for pattern_name in infer_patterns_from_slug(row.slug):
                pid = _pattern_id(conn, pattern_name)
                if pid is None:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO problem_patterns (problem_slug, pattern_id) "
                    "VALUES (?, ?)",
                    (row.slug, pid),
                )
                if cur.rowcount > 0:
                    n_pattern_assignments += 1

    return {
        "companies": n_companies,
        "problems": n_problems,
        "problem_stubs": n_problem_stubs,
        "pattern_assignments": n_pattern_assignments,
    }


def get_companies_with_counts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.name, c.last_ingested_at, COUNT(cp.problem_slug) AS n_problems,
               SUM(cp.confidence) AS total_confidence
        FROM companies c
        LEFT JOIN company_problems cp ON cp.company = c.name
        GROUP BY c.name
        ORDER BY n_problems DESC, c.name
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def get_company_problems(
    conn: sqlite3.Connection, company: str, limit: int = 25
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT problem_slug, leetcode_id, title, difficulty,
               appearances_json, confidence
        FROM company_problems
        WHERE company = ?
        ORDER BY confidence DESC, title
        LIMIT ?
        """,
        (company, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["appearances"] = json.loads(d.pop("appearances_json") or "[]")
        except json.JSONDecodeError:
            d["appearances"] = []
        out.append(d)
    return out


# --- Spaced repetition (SM-2) ---------------------------------------------
# `_max_hint_level_for_attempt` is shared with the mastery code above.


def update_review_for_attempt(
    conn: sqlite3.Connection, attempt_id: int
) -> Optional[dict]:
    """Apply one SM-2 step for the problem this attempt belonged to.

    Returns a dict describing the new review state, or None if the attempt
    isn't finished or the problem doesn't exist.
    """
    from datetime import date

    from lc_coach.schedule import (
        INITIAL_EASE,
        ReviewState,
        next_due_date,
        quality_for_attempt,
        update_review,
    )

    attempt = conn.execute(
        "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    if attempt is None or attempt["outcome"] is None:
        return None
    slug = attempt["problem_slug"]

    existing = conn.execute(
        "SELECT ease, repetitions, interval_days FROM reviews WHERE problem_slug = ?",
        (slug,),
    ).fetchone()
    state = (
        ReviewState(
            ease=float(existing["ease"]),
            repetitions=int(existing["repetitions"]),
            interval_days=int(existing["interval_days"]),
        )
        if existing
        else ReviewState()
    )

    max_level = _max_hint_level_for_attempt(conn, attempt_id)
    q = quality_for_attempt(attempt["outcome"], max_level)
    new_state = update_review(state, q)
    today = date.today()
    due = next_due_date(today, new_state.interval_days)

    conn.execute(
        """
        INSERT INTO reviews
          (problem_slug, ease, repetitions, interval_days, due_date,
           last_quality, last_reviewed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(problem_slug) DO UPDATE SET
          ease = excluded.ease,
          repetitions = excluded.repetitions,
          interval_days = excluded.interval_days,
          due_date = excluded.due_date,
          last_quality = excluded.last_quality,
          last_reviewed_at = excluded.last_reviewed_at
        """,
        (
            slug,
            new_state.ease,
            new_state.repetitions,
            new_state.interval_days,
            due.isoformat(),
            q,
            _now_iso(),
        ),
    )

    return {
        "problem_slug": slug,
        "quality": q,
        "ease": new_state.ease,
        "repetitions": new_state.repetitions,
        "interval_days": new_state.interval_days,
        "due_date": due.isoformat(),
    }


def get_due_problems(
    conn: sqlite3.Connection, today: Optional[str] = None, limit: int = 20
) -> list[dict]:
    from datetime import date

    today_str = today or date.today().isoformat()
    rows = conn.execute(
        """
        SELECT r.problem_slug, p.title, p.difficulty,
               r.due_date, r.interval_days, r.repetitions, r.ease,
               r.last_quality, r.last_reviewed_at
        FROM reviews r
        LEFT JOIN problems p ON p.slug = r.problem_slug
        WHERE r.due_date <= ?
        ORDER BY r.due_date ASC, r.last_reviewed_at ASC
        LIMIT ?
        """,
        (today_str, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Recommender support queries -----------------------------------------


def load_all_company_profiles(conn: sqlite3.Connection) -> dict:
    """Build {company_name: CompanyProfile} from company_problems.

    Profile holds the set of slugs (for Jaccard) and a tally of difficulty
    counts (for the difficulty-distribution cosine).
    """
    from lc_coach.recommend import CompanyProfile

    rows = conn.execute(
        "SELECT company, problem_slug, difficulty FROM company_problems"
    ).fetchall()
    profiles: dict[str, CompanyProfile] = {}
    for r in rows:
        company = r["company"]
        prof = profiles.get(company)
        if prof is None:
            prof = CompanyProfile(name=company)
            profiles[company] = prof
        prof.slugs.add(r["problem_slug"])
        diff = (r["difficulty"] or "").strip()
        if diff in ("Easy", "Medium", "Hard"):
            prof.difficulty_counts[diff] = prof.difficulty_counts.get(diff, 0) + 1
    return profiles


def get_pool_entries(conn: sqlite3.Connection, company: str) -> list:
    from lc_coach.recommend import PoolEntry

    rows = conn.execute(
        """
        SELECT problem_slug, title, difficulty, confidence
        FROM company_problems
        WHERE company = ?
        ORDER BY confidence DESC
        """,
        (company,),
    ).fetchall()
    return [
        PoolEntry(
            slug=r["problem_slug"],
            company=company,
            confidence=float(r["confidence"]),
            title=r["title"],
            difficulty=r["difficulty"],
        )
        for r in rows
    ]


def get_problem_pattern_map(
    conn: sqlite3.Connection, slugs: list[str]
) -> dict[str, list[str]]:
    """For the slugs we've actually registered + tagged, return a map
    {slug: [pattern_name, ...]}. Slugs without a row in problem_patterns are
    omitted — the caller treats absence as 'no pattern info'."""
    if not slugs:
        return {}
    placeholders = ",".join("?" * len(slugs))
    rows = conn.execute(
        f"""
        SELECT pp.problem_slug, p.name AS pattern_name
        FROM problem_patterns pp
        JOIN patterns p ON p.id = pp.pattern_id
        WHERE pp.problem_slug IN ({placeholders})
        """,
        slugs,
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["problem_slug"], []).append(r["pattern_name"])
    return out


def get_recent_attempt_slugs(
    conn: sqlite3.Connection, *, days: int = 7
) -> set[str]:
    """Slugs the user has attempted in the recent past — used to de-emphasize
    repeats in the recommender."""
    rows = conn.execute(
        """
        SELECT DISTINCT problem_slug FROM attempts
        WHERE started_at >= datetime('now', ?)
        """,
        (f"-{int(days)} days",),
    ).fetchall()
    return {r["problem_slug"] for r in rows}


def get_weakest_pattern_names(
    conn: sqlite3.Connection, *, n: int = 3
) -> list[str]:
    """Names only — for the recommender's weak-pattern bonus."""
    rows = get_weakest_patterns(conn, n=n)
    return [r["name"] for r in rows]


def get_pattern_elo(conn: sqlite3.Connection, pattern_name: str) -> float:
    from lc_coach.mastery import INITIAL_PATTERN_ELO

    row = conn.execute(
        """
        SELECT m.elo FROM mastery m
        JOIN patterns p ON p.id = m.pattern_id
        WHERE p.name = ?
        """,
        (pattern_name,),
    ).fetchone()
    return float(row["elo"]) if row else INITIAL_PATTERN_ELO


def get_problems_by_pattern(
    conn: sqlite3.Connection, pattern_name: str, *, limit: int = 200
) -> list:
    """Return SkillCandidates: one per problem tagged with this pattern.
    Confidence is the max across all companies that ingested the problem
    (0 if untagged by any company)."""
    from lc_coach.recommend import SkillCandidate

    rows = conn.execute(
        """
        SELECT p.slug, p.title, p.difficulty,
               COALESCE(MAX(cp.confidence), 0.0) AS confidence
        FROM patterns pat
        JOIN problem_patterns pp ON pp.pattern_id = pat.id
        JOIN problems p ON p.slug = pp.problem_slug
        LEFT JOIN company_problems cp ON cp.problem_slug = p.slug
        WHERE pat.name = ?
        GROUP BY p.slug, p.title, p.difficulty
        ORDER BY confidence DESC
        LIMIT ?
        """,
        (pattern_name, limit),
    ).fetchall()
    return [
        SkillCandidate(
            slug=r["slug"],
            title=r["title"],
            difficulty=r["difficulty"],
            confidence=float(r["confidence"]),
        )
        for r in rows
    ]


def get_pattern_id_by_name(
    conn: sqlite3.Connection, name: str
) -> Optional[int]:
    return _pattern_id(conn, name)


# --- Premium-only LeetCode problems (user-marked) -------------------------


def mark_premium(conn: sqlite3.Connection, slug: str) -> None:
    """Idempotent: flag a slug as premium-only so the recommender never
    surfaces it again."""
    conn.execute(
        "INSERT OR IGNORE INTO premium_slugs (slug, marked_at) VALUES (?, ?)",
        (slug, _now_iso()),
    )


def unmark_premium(conn: sqlite3.Connection, slug: str) -> None:
    conn.execute("DELETE FROM premium_slugs WHERE slug = ?", (slug,))


def get_premium_slugs(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT slug FROM premium_slugs").fetchall()
    return {r[0] for r in rows}


def list_premium_slugs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT slug, marked_at FROM premium_slugs ORDER BY marked_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]
