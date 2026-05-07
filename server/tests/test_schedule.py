import math
from datetime import date

import pytest

from lc_coach.schedule import (
    INITIAL_EASE,
    MIN_EASE,
    ReviewState,
    next_due_date,
    quality_for_attempt,
    update_review,
)


# --- quality mapping ------------------------------------------------------


def test_quality_solved_no_hint_is_5():
    assert quality_for_attempt("solved", 0) == 5


def test_quality_solved_with_hints_drops():
    assert quality_for_attempt("solved", 1) == 4
    assert quality_for_attempt("solved", 2) == 3
    assert quality_for_attempt("solved", 3) == 3  # floor at 3


def test_quality_partial():
    assert quality_for_attempt("partial", 0) == 3
    assert quality_for_attempt("partial", 1) == 2
    assert quality_for_attempt("partial", 2) == 2


def test_quality_stuck():
    assert quality_for_attempt("stuck", 0) == 1
    assert quality_for_attempt("stuck", 1) == 0
    assert quality_for_attempt("stuck", 3) == 0


def test_quality_unknown_outcome():
    with pytest.raises(ValueError):
        quality_for_attempt("blew-up", 0)


# --- SM-2 update math ----------------------------------------------------


def test_update_review_invalid_q():
    with pytest.raises(ValueError):
        update_review(ReviewState(), q=-1)
    with pytest.raises(ValueError):
        update_review(ReviewState(), q=6)


def test_update_review_q_lt_3_resets():
    state = ReviewState(ease=2.5, repetitions=4, interval_days=30)
    new = update_review(state, q=2)
    assert new.repetitions == 0
    assert new.interval_days == 1
    assert new.ease < state.ease  # ease should drop on a poor recall


def test_update_review_first_pass_intervals():
    s0 = ReviewState()
    s1 = update_review(s0, q=5)
    assert s1.repetitions == 1
    assert s1.interval_days == 1

    s2 = update_review(s1, q=5)
    assert s2.repetitions == 2
    assert s2.interval_days == 6

    s3 = update_review(s2, q=5)
    assert s3.repetitions == 3
    # third+ interval = round(prev_interval * ease). After two perfect
    # recalls, ease should have ticked up slightly above 2.5.
    assert s3.interval_days >= 6 * INITIAL_EASE - 1


def test_update_review_ease_floor():
    state = ReviewState(ease=MIN_EASE, repetitions=0, interval_days=1)
    new = update_review(state, q=0)
    assert new.ease >= MIN_EASE


def test_next_due_date():
    today = date(2026, 5, 7)
    assert next_due_date(today, 0).isoformat() == "2026-05-07"
    assert next_due_date(today, 6).isoformat() == "2026-05-13"
    assert next_due_date(today, -5).isoformat() == "2026-05-07"  # clamped to >=0
