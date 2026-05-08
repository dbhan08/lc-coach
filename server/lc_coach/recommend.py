"""Company similarity + next-problem recommender.

Two pure-math chunks live here:

1. **Company similarity** — given the ingested company question lists, score
   pairs of companies by how similar their interview profiles are. v1 uses:
       similarity = α · Jaccard(question_sets) + β · cosine(difficulty_dist)
   No topic-distribution term in v1: most ingested problems aren't tagged
   with our coarse patterns yet (tagging happens lazily when a problem is
   opened in the side panel and POSTed to /problems). Adding a topic
   component is a v2 enhancement once the user has clicked through enough
   problems to populate `problem_patterns` densely.

2. **Next-problem scoring** — for a target company, candidate problems come
   from the target's pool plus, on cold-start, the union with top-k similar
   companies. Each candidate is scored against the user's current state
   (weakest patterns, SM-2 due set, recent-attempt penalty).

Persistence and DB queries live in `db.py`; this module is the math.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

# --- tunables -----------------------------------------------------------

ALPHA_JACCARD = 0.7
BETA_DIFFICULTY = 0.3

# Cold-start: expand pool when target's high-confidence problem count is
# under this threshold.
COLD_START_THRESHOLD = 30

# Minimum confidence per row to count as "high-confidence" for the
# cold-start gate.
HIGH_CONFIDENCE_FLOOR = 0.4

# Scoring weights for next-problem selection (company mode)
W_POOL = 1.0
W_WEAK = 1.5
W_DUE = 1.2
W_RECENT_PENALTY = 0.8

# Scoring weights for skill / improve mode
W_SKILL_POOL = 0.8
W_SKILL_DIFFICULTY = 1.5
W_SKILL_DUE = 1.0
W_SKILL_RECENT_PENALTY = 1.2


# --- shapes -------------------------------------------------------------


@dataclass
class CompanyProfile:
    name: str
    slugs: set[str] = field(default_factory=set)
    difficulty_counts: dict[str, int] = field(default_factory=dict)

    @property
    def n_problems(self) -> int:
        return len(self.slugs)

    def difficulty_distribution(self) -> tuple[float, float, float]:
        total = sum(self.difficulty_counts.values()) or 1
        return (
            self.difficulty_counts.get("Easy", 0) / total,
            self.difficulty_counts.get("Medium", 0) / total,
            self.difficulty_counts.get("Hard", 0) / total,
        )


# --- similarity --------------------------------------------------------


def _cosine(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def similarity(a: CompanyProfile, b: CompanyProfile) -> float:
    j = _jaccard(a.slugs, b.slugs)
    c = _cosine(a.difficulty_distribution(), b.difficulty_distribution())
    return ALPHA_JACCARD * j + BETA_DIFFICULTY * c


def rank_similar(
    target: CompanyProfile,
    others: Iterable[CompanyProfile],
    *,
    k: int = 5,
) -> list[tuple[CompanyProfile, float]]:
    """Return top-k companies by similarity to target, descending."""
    scored = [
        (other, similarity(target, other))
        for other in others
        if other.name != target.name
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


# --- cold-start expansion ---------------------------------------------


@dataclass
class PoolEntry:
    slug: str
    company: str  # which company contributed this entry
    confidence: float
    title: Optional[str] = None
    difficulty: Optional[str] = None


def needs_cold_start(target: CompanyProfile, *, threshold: int = COLD_START_THRESHOLD) -> bool:
    return target.n_problems < threshold


def expand_pool(
    target: CompanyProfile,
    similar: list[tuple[CompanyProfile, float]],
    *,
    target_entries: list[PoolEntry],
    similar_entries_by_company: dict[str, list[PoolEntry]],
) -> list[PoolEntry]:
    """Combine target's entries with similar-company entries, deduping by
    slug. Target entries always win on conflict (higher implicit weight)."""
    seen: set[str] = set()
    out: list[PoolEntry] = []
    for entry in target_entries:
        if entry.slug in seen:
            continue
        seen.add(entry.slug)
        out.append(entry)
    for company, score in similar:
        for entry in similar_entries_by_company.get(company.name, []):
            if entry.slug in seen:
                continue
            seen.add(entry.slug)
            # Down-weight similar-company entries by the similarity score so
            # closer-neighbor problems rank higher than distant-neighbor ones.
            out.append(
                PoolEntry(
                    slug=entry.slug,
                    company=entry.company,
                    confidence=entry.confidence * max(0.1, score),
                    title=entry.title,
                    difficulty=entry.difficulty,
                )
            )
    return out


# --- next-problem scoring ---------------------------------------------


@dataclass
class CandidateScore:
    slug: str
    company: str
    title: Optional[str]
    difficulty: Optional[str]
    score: float
    pool_score: float
    weak_score: float
    due_score: float
    recent_penalty: float
    rationale_parts: list[str] = field(default_factory=list)


def score_candidate(
    entry: PoolEntry,
    *,
    weak_patterns_by_slug: dict[str, list[str]],
    due_slugs: set[str],
    recent_slugs: set[str],
    user_weak_patterns: list[str],
    target_name: str,
) -> CandidateScore:
    """Compute the multi-objective score for one candidate problem."""
    pool_score = entry.confidence

    weak_hit = []
    if user_weak_patterns and entry.slug in weak_patterns_by_slug:
        problem_patterns = set(weak_patterns_by_slug[entry.slug])
        weak_hit = [p for p in user_weak_patterns if p in problem_patterns]
    weak_score = 1.0 if weak_hit else 0.0

    due_score = 1.0 if entry.slug in due_slugs else 0.0
    recent_penalty = 1.0 if entry.slug in recent_slugs else 0.0

    total = (
        W_POOL * pool_score
        + W_WEAK * weak_score
        + W_DUE * due_score
        - W_RECENT_PENALTY * recent_penalty
    )

    parts: list[str] = []
    if entry.company == target_name:
        parts.append(f"from {target_name}'s tagged set")
    else:
        parts.append(f"from {entry.company} (similar to {target_name})")
    if weak_hit:
        parts.append("hits " + "/".join(weak_hit) + " — your weak pattern")
    if entry.slug in due_slugs:
        parts.append("due for review")
    if entry.slug in recent_slugs:
        parts.append("(de-emphasized: recently attempted)")

    return CandidateScore(
        slug=entry.slug,
        company=entry.company,
        title=entry.title,
        difficulty=entry.difficulty,
        score=total,
        pool_score=pool_score,
        weak_score=weak_score,
        due_score=due_score,
        recent_penalty=recent_penalty,
        rationale_parts=parts,
    )


def pick_next(
    pool: list[PoolEntry],
    *,
    weak_patterns_by_slug: dict[str, list[str]],
    due_slugs: set[str],
    recent_slugs: set[str],
    user_weak_patterns: list[str],
    target_name: str,
) -> Optional[CandidateScore]:
    if not pool:
        return None
    scored = [
        score_candidate(
            e,
            weak_patterns_by_slug=weak_patterns_by_slug,
            due_slugs=due_slugs,
            recent_slugs=recent_slugs,
            user_weak_patterns=user_weak_patterns,
            target_name=target_name,
        )
        for e in pool
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[0]


# --- Skill / Improve mode -------------------------------------------------


def difficulty_target_for_elo(pattern_elo: float) -> tuple[str, set[str]]:
    """Pick the appropriate-difficulty bucket for a user's pattern Elo.

    Returns (preferred_difficulty, also_acceptable_set). Preferred earns the
    full difficulty bonus; "also acceptable" earns half. Anything else gets 0.
    """
    if pattern_elo < 1100:
        return ("Easy", {"Medium"})
    if pattern_elo < 1400:
        return ("Medium", {"Easy", "Hard"})
    return ("Hard", {"Medium"})


def difficulty_match_score(
    candidate_difficulty: Optional[str], pattern_elo: float
) -> float:
    if not candidate_difficulty:
        return 0.0
    diff = candidate_difficulty.strip()
    preferred, acceptable = difficulty_target_for_elo(pattern_elo)
    if diff == preferred:
        return 1.0
    if diff in acceptable:
        return 0.5
    return 0.0


@dataclass
class SkillCandidate:
    slug: str
    title: Optional[str]
    difficulty: Optional[str]
    confidence: float  # max company-confidence for this slug
    rationale_parts: list[str] = field(default_factory=list)
    score: float = 0.0
    diff_match: float = 0.0


def score_skill_candidate(
    entry: SkillCandidate,
    *,
    pattern_elo: float,
    due_slugs: set[str],
    recent_slugs: set[str],
    pattern_name: str,
) -> SkillCandidate:
    diff_match = difficulty_match_score(entry.difficulty, pattern_elo)
    due_score = 1.0 if entry.slug in due_slugs else 0.0
    recent_penalty = 1.0 if entry.slug in recent_slugs else 0.0

    total = (
        W_SKILL_POOL * entry.confidence
        + W_SKILL_DIFFICULTY * diff_match
        + W_SKILL_DUE * due_score
        - W_SKILL_RECENT_PENALTY * recent_penalty
    )

    parts = [f"hits {pattern_name}"]
    preferred, _ = difficulty_target_for_elo(pattern_elo)
    if entry.difficulty:
        if entry.difficulty == preferred:
            parts.append(
                f"{entry.difficulty} matches your Elo on this pattern"
            )
        else:
            parts.append(f"{entry.difficulty} (off the ideal {preferred})")
    if entry.slug in due_slugs:
        parts.append("due for review")
    if entry.slug in recent_slugs:
        parts.append("(de-emphasized: recently attempted)")

    entry.score = total
    entry.diff_match = diff_match
    entry.rationale_parts = parts
    return entry


def pick_skill_next(
    candidates: list[SkillCandidate],
    *,
    pattern_elo: float,
    due_slugs: set[str],
    recent_slugs: set[str],
    pattern_name: str,
) -> Optional[SkillCandidate]:
    if not candidates:
        return None
    scored = [
        score_skill_candidate(
            c,
            pattern_elo=pattern_elo,
            due_slugs=due_slugs,
            recent_slugs=recent_slugs,
            pattern_name=pattern_name,
        )
        for c in candidates
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[0]
