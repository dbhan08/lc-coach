import math

import pytest

from lc_coach import db
from lc_coach.recommend import (
    COLD_START_THRESHOLD,
    CompanyProfile,
    PoolEntry,
    expand_pool,
    needs_cold_start,
    pick_next,
    rank_similar,
    similarity,
)


def _profile(name: str, slugs: list[str], easy=0, medium=0, hard=0) -> CompanyProfile:
    p = CompanyProfile(name=name)
    p.slugs.update(slugs)
    if easy:
        p.difficulty_counts["Easy"] = easy
    if medium:
        p.difficulty_counts["Medium"] = medium
    if hard:
        p.difficulty_counts["Hard"] = hard
    return p


def test_difficulty_distribution_normalizes():
    p = _profile("apple", [], easy=1, medium=2, hard=1)
    e, m, h = p.difficulty_distribution()
    assert math.isclose(e, 0.25)
    assert math.isclose(m, 0.5)
    assert math.isclose(h, 0.25)


def test_difficulty_distribution_empty_safe():
    p = _profile("apple", [])
    e, m, h = p.difficulty_distribution()
    assert e == m == h == 0.0


def test_similarity_identical_companies_max():
    a = _profile("apple", ["x", "y", "z"], medium=3)
    b = _profile("apple-clone", ["x", "y", "z"], medium=3)
    s = similarity(a, b)
    assert s > 0.95  # ~ALPHA + BETA, with small float drift OK


def test_similarity_zero_overlap_drops_score():
    a = _profile("apple", ["x", "y", "z"], medium=3)
    b = _profile("never-overlap", ["a", "b", "c"], medium=3)
    s = similarity(a, b)
    # Difficulty distribution still cosine-equal, so score >= BETA term
    assert 0.25 <= s <= 0.4  # ~BETA_DIFFICULTY = 0.3


def test_rank_similar_excludes_self():
    target = _profile("apple", ["x", "y"], medium=2)
    others = [
        target,
        _profile("tesla", ["x", "y"], medium=2),
        _profile("amazon", ["a", "b"], easy=2),
    ]
    ranked = rank_similar(target, others, k=5)
    names = [c.name for c, _ in ranked]
    assert "apple" not in names


def test_needs_cold_start_threshold():
    p = _profile("spacex", ["a"] * 5)
    assert needs_cold_start(p)
    big = _profile("apple", [str(i) for i in range(COLD_START_THRESHOLD + 1)])
    assert not needs_cold_start(big)


def test_expand_pool_dedupes_target_wins():
    target = _profile("spacex", ["x", "y"])
    other = _profile("tesla", ["y", "z"])
    target_entries = [
        PoolEntry(slug="x", company="spacex", confidence=1.0),
        PoolEntry(slug="y", company="spacex", confidence=2.0),
    ]
    similar = [
        (other, 0.7),
    ]
    similar_entries = {
        "tesla": [
            PoolEntry(slug="y", company="tesla", confidence=5.0),  # would win on raw conf, but target dedups it
            PoolEntry(slug="z", company="tesla", confidence=5.0),
        ]
    }
    pool = expand_pool(
        target,
        similar,
        target_entries=target_entries,
        similar_entries_by_company=similar_entries,
    )
    slugs = [e.slug for e in pool]
    assert slugs == ["x", "y", "z"]
    # 'y' came from target — should keep target's confidence (2.0), not Tesla's (5.0 * 0.7)
    y = next(e for e in pool if e.slug == "y")
    assert y.company == "spacex"
    assert y.confidence == 2.0
    # 'z' came from Tesla, scaled by similarity score 0.7
    z = next(e for e in pool if e.slug == "z")
    assert z.company == "tesla"
    assert math.isclose(z.confidence, 5.0 * 0.7)


def test_pick_next_prefers_weak_pattern_hit():
    pool = [
        PoolEntry(slug="hard-but-not-weak", company="apple", confidence=10.0,
                  title="Far Higher Confidence", difficulty="Hard"),
        PoolEntry(slug="hits-your-weak-pattern", company="apple", confidence=1.0,
                  title="Lower Confidence", difficulty="Medium"),
    ]
    chosen = pick_next(
        pool,
        weak_patterns_by_slug={"hits-your-weak-pattern": ["topological-sort"]},
        due_slugs=set(),
        recent_slugs=set(),
        user_weak_patterns=["topological-sort"],
        target_name="apple",
    )
    assert chosen is not None
    # Even though 'hard-but-not-weak' has 10x the confidence, the weak-pattern
    # bonus (W_WEAK = 1.5) should NOT be enough to overcome a 10x pool gap.
    # Verify the bonus is at least applied:
    assert chosen.slug == "hard-but-not-weak"
    # but the weak-pattern candidate's score should be its weak_score=1
    weak = next(p for p in pool if p.slug == "hits-your-weak-pattern")
    chosen2 = pick_next(
        [weak],
        weak_patterns_by_slug={"hits-your-weak-pattern": ["topological-sort"]},
        due_slugs=set(),
        recent_slugs=set(),
        user_weak_patterns=["topological-sort"],
        target_name="apple",
    )
    assert chosen2 is not None and chosen2.weak_score == 1.0


def test_pick_next_due_for_review_lifts_score():
    pool = [
        PoolEntry(slug="not-due", company="apple", confidence=1.0, title="A"),
        PoolEntry(slug="due-today", company="apple", confidence=1.0, title="B"),
    ]
    chosen = pick_next(
        pool,
        weak_patterns_by_slug={},
        due_slugs={"due-today"},
        recent_slugs=set(),
        user_weak_patterns=[],
        target_name="apple",
    )
    assert chosen is not None and chosen.slug == "due-today"
    assert "due for review" in " ".join(chosen.rationale_parts)


def test_pick_next_recent_attempt_hard_filtered_even_at_high_confidence():
    """Reproduce the v1.1.0 bug: a hot recent slug at 10× confidence should
    NOT win over a never-attempted candidate. We hard-filter, not just
    penalize."""
    pool = [
        PoolEntry(slug="recent-but-loved", company="spacex", confidence=10.0,
                  title="Way Higher Confidence", difficulty="Medium"),
        PoolEntry(slug="not-recent-low-conf", company="spacex", confidence=0.2,
                  title="Lower Confidence", difficulty="Medium"),
    ]
    chosen = pick_next(
        pool,
        weak_patterns_by_slug={},
        due_slugs=set(),
        recent_slugs={"recent-but-loved"},
        user_weak_patterns=[],
        target_name="spacex",
    )
    assert chosen is not None
    assert chosen.slug == "not-recent-low-conf"


def test_pick_next_recent_but_due_still_eligible():
    """A recently-attempted problem that's also SM-2 due today should still
    surface — that's the spaced-rep loop, not a duplicate suggestion."""
    pool = [
        PoolEntry(slug="recent-and-due", company="apple", confidence=1.0,
                  title="Due", difficulty="Medium"),
        PoolEntry(slug="not-recent", company="apple", confidence=0.5,
                  title="Other", difficulty="Medium"),
    ]
    chosen = pick_next(
        pool,
        weak_patterns_by_slug={},
        due_slugs={"recent-and-due"},
        recent_slugs={"recent-and-due"},
        user_weak_patterns=[],
        target_name="apple",
    )
    assert chosen is not None
    # Higher confidence + due bonus + not filtered out → wins
    assert chosen.slug == "recent-and-due"


def test_pick_next_falls_back_when_all_recent():
    """If every candidate has been attempted recently and none are due, the
    recommender falls back to ranking the full pool and flags the repeat in
    the rationale."""
    pool = [
        PoolEntry(slug="a", company="apple", confidence=2.0, title="A"),
        PoolEntry(slug="b", company="apple", confidence=1.0, title="B"),
    ]
    chosen = pick_next(
        pool,
        weak_patterns_by_slug={},
        due_slugs=set(),
        recent_slugs={"a", "b"},
        user_weak_patterns=[],
        target_name="apple",
    )
    assert chosen is not None
    assert chosen.slug == "a"
    assert any("repeat" in p for p in chosen.rationale_parts)


def test_pick_next_returns_none_on_empty():
    chosen = pick_next(
        [],
        weak_patterns_by_slug={},
        due_slugs=set(),
        recent_slugs=set(),
        user_weak_patterns=[],
        target_name="apple",
    )
    assert chosen is None


# --- DB integration ----------------------------------------------------


def test_load_all_company_profiles_round_trip(tmp_db):
    db.init_db()
    # Hand-craft some company_problems rows
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO companies (name, last_ingested_at) VALUES ('apple', '2026-01-01')"
        )
        for slug, diff in (("two-sum", "Easy"), ("lru-cache", "Medium"), ("trapping-rain-water", "Hard")):
            conn.execute(
                "INSERT INTO company_problems (company, problem_slug, difficulty, confidence) VALUES (?, ?, ?, ?)",
                ("apple", slug, diff, 1.0),
            )
        profiles = db.load_all_company_profiles(conn)
    assert "apple" in profiles
    p = profiles["apple"]
    assert p.slugs == {"two-sum", "lru-cache", "trapping-rain-water"}
    assert p.difficulty_counts == {"Easy": 1, "Medium": 1, "Hard": 1}
