import math

import pytest

from lc_coach import db
from lc_coach.mastery import (
    INITIAL_PATTERN_ELO,
    K_FACTOR,
    attempt_score,
    elo_update,
    expected_score,
    map_leetcode_tags_to_patterns,
    problem_effective_elo,
)


def test_map_leetcode_tags_basic():
    tags = ["Array", "Hash Table", "Two Pointers"]
    out = map_leetcode_tags_to_patterns(tags)
    assert out == ["arrays", "hashing", "two-pointer"]


def test_map_leetcode_tags_dedup_and_unknowns():
    tags = ["Array", "Sorting", "WeirdNewTag", "Array"]
    out = map_leetcode_tags_to_patterns(tags)
    # Sorting also maps to "arrays" — should be deduplicated
    assert out == ["arrays"]


def test_map_leetcode_tags_empty():
    assert map_leetcode_tags_to_patterns([]) == []
    assert map_leetcode_tags_to_patterns(["", "  "]) == []


def test_problem_effective_elo():
    assert problem_effective_elo("Easy") == 1100.0
    assert problem_effective_elo("medium") == 1500.0
    assert problem_effective_elo("HARD") == 1900.0
    assert problem_effective_elo(None) == 1300.0
    assert problem_effective_elo("Whatever") == 1300.0


def test_attempt_score_table():
    assert attempt_score("solved", 0) == 1.0
    assert math.isclose(attempt_score("solved", 1), 0.9)
    assert math.isclose(attempt_score("solved", 2), 0.8)
    assert math.isclose(attempt_score("solved", 3), 0.7)
    assert attempt_score("partial", 0) == 0.5
    assert math.isclose(attempt_score("partial", 1), 0.4)
    assert attempt_score("stuck", 0) == 0.0
    # Stuck with hint cannot go below 0
    assert attempt_score("stuck", 3) == 0.0


def test_attempt_score_invalid():
    with pytest.raises(ValueError):
        attempt_score("blew-up", 0)
    with pytest.raises(ValueError):
        attempt_score("solved", -1)


def test_expected_score_symmetry():
    # When ratings equal, expected score = 0.5
    assert math.isclose(expected_score(1500.0, 1500.0), 0.5)
    # When pattern is much higher, expected ~ 1
    assert expected_score(2000.0, 1200.0) > 0.95
    # When pattern is much lower, expected ~ 0
    assert expected_score(1000.0, 2000.0) < 0.05


def test_elo_update_direction_solving_easy_below_expectation():
    # Pattern at 1500, problem effective Easy=1100. Pattern is favored;
    # solving with no hints (score=1) should still bump it up but only a
    # little because expected was already high.
    new = elo_update(1500.0, 1100.0, 1.0, k=K_FACTOR)
    delta = new - 1500.0
    assert 0 < delta < K_FACTOR  # less than K because expected was close to 1


def test_elo_update_direction_failing_hard():
    # Pattern at 1200, problem Hard=1900. Pattern is heavy underdog. Stuck
    # (score=0) should drop them only a little because expected was already low.
    new = elo_update(1200.0, 1900.0, 0.0, k=K_FACTOR)
    delta = new - 1200.0
    assert -K_FACTOR < delta < 0


def test_elo_update_zero_change_when_score_matches_expected():
    e = expected_score(1300.0, 1500.0)
    new = elo_update(1300.0, 1500.0, e, k=K_FACTOR)
    assert math.isclose(new, 1300.0)


def test_end_to_end_attempt_shifts_mastery_in_db(tmp_db, monkeypatch):
    """An end-to-end check against the real DB: tag a problem with patterns,
    finish an attempt, verify mastery rows were created with sensible Elos.
    """
    db.init_db()
    with db.connect() as conn:
        db.upsert_problem(
            conn,
            slug="two-sum",
            title="Two Sum",
            statement="...",
            difficulty="Easy",
            tags=["Array", "Hash Table"],
        )
        attached = db.assign_patterns_to_problem(
            conn, slug="two-sum", pattern_names=["arrays", "hashing"]
        )
        assert set(attached) == {"arrays", "hashing"}

        # Solve cleanly with no hints — score = 1.0 against an Easy
        a = db.start_attempt(conn, problem_slug="two-sum")
        db.finish_attempt(
            conn,
            attempt_id=a["id"],
            outcome="solved",
            code_snapshot="...",
        )
        updates = db.update_mastery_for_attempt(conn, a["id"])

    assert len(updates) == 2
    for u in updates:
        # Easy is below INITIAL_PATTERN_ELO (1100 < 1200), so solving it with
        # score=1.0 should still nudge Elo up — but only a little, because
        # expected score was already > 0.5.
        assert u["new_elo"] > u["old_elo"]
        assert u["delta"] < K_FACTOR / 2  # diminishing returns on easy
        assert u["n_attempts"] == 1


def test_repeated_attempts_compound_changes(tmp_db):
    db.init_db()
    with db.connect() as conn:
        db.upsert_problem(
            conn,
            slug="course-schedule",
            title="Course Schedule",
            statement="...",
            difficulty="Medium",
            tags=["Topological Sort"],
        )
        db.assign_patterns_to_problem(
            conn,
            slug="course-schedule",
            pattern_names=["topological-sort"],
        )

        # Three "stuck" attempts on a Medium should drag topological-sort Elo
        # down meaningfully.
        for _ in range(3):
            a = db.start_attempt(conn, problem_slug="course-schedule")
            db.finish_attempt(
                conn, attempt_id=a["id"], outcome="stuck", code_snapshot=None
            )
            db.update_mastery_for_attempt(conn, a["id"])

        weakest = db.get_weakest_patterns(conn, n=5)
    names = [w["name"] for w in weakest]
    assert "topological-sort" in names
    top = next(w for w in weakest if w["name"] == "topological-sort")
    assert top["elo"] < INITIAL_PATTERN_ELO
    assert top["n_attempts"] == 3


def test_get_full_mastery_returns_all_patterns(tmp_db):
    from lc_coach.mastery import COARSE_PATTERNS

    db.init_db()
    with db.connect() as conn:
        full = db.get_full_mastery(conn)
    assert {row["name"] for row in full} == set(COARSE_PATTERNS)
    # Untouched patterns default to elo=1200, n_attempts=0
    for row in full:
        assert row["elo"] == 1200.0
        assert row["n_attempts"] == 0
