"""SM-2 spaced repetition.

Standard SuperMemo-2: ease factor (EF), repetitions, interval in days. Each
review yields a quality grade q in {0..5}; the algorithm updates EF, the
repetition count, and the next interval.

We map each finished attempt to a quality grade like this:

    outcome   max_hint_level  quality
    solved    0               5
    solved    1               4
    solved    2               3
    solved    3               3
    partial   0               3
    partial   1               2
    partial   2+              2
    stuck     0               1
    stuck     1+              0

q < 3 resets the repetition counter and pushes the next review out 1 day —
this is canonical SM-2 behavior for "you didn't really know it."

This module is pure math + state shaping. Persistence lives in db.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

INITIAL_EASE = 2.5
MIN_EASE = 1.3


@dataclass
class ReviewState:
    ease: float = INITIAL_EASE
    repetitions: int = 0
    interval_days: int = 0


def quality_for_attempt(outcome: str, max_hint_level: int) -> int:
    """Map (outcome, max hint level used) → SM-2 quality grade in {0..5}."""
    o = (outcome or "").lower()
    if o == "solved":
        return max(3, 5 - max_hint_level)  # 5, 4, 3, 3
    if o == "partial":
        if max_hint_level == 0:
            return 3
        if max_hint_level == 1:
            return 2
        return 2
    if o == "stuck":
        return 0 if max_hint_level >= 1 else 1
    raise ValueError(f"unknown outcome: {outcome!r}")


def update_review(state: ReviewState, q: int) -> ReviewState:
    """Apply one SM-2 step. Returns a new ReviewState."""
    if q < 0 or q > 5:
        raise ValueError(f"quality must be in 0..5 (got {q})")

    new_ease = state.ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    new_ease = max(MIN_EASE, new_ease)

    if q < 3:
        return ReviewState(ease=new_ease, repetitions=0, interval_days=1)

    new_reps = state.repetitions + 1
    if new_reps == 1:
        new_interval = 1
    elif new_reps == 2:
        new_interval = 6
    else:
        new_interval = max(1, round(state.interval_days * new_ease))

    return ReviewState(
        ease=new_ease, repetitions=new_reps, interval_days=new_interval
    )


def next_due_date(today: date, interval_days: int) -> date:
    return today + timedelta(days=max(0, interval_days))
