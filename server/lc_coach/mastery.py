"""Per-pattern Elo mastery model.

Pure functions only. DB persistence lives in `db.py`; orchestration of
"finish attempt → recompute mastery" lives there too. This module is the
math + the static taxonomy.

Design decisions:
- ~20 coarse pattern buckets (not 50+ raw LeetCode tags). Coarser buckets
  mean more attempts per bucket, which means Elo has a real signal sooner.
- Each problem can map to multiple patterns. Mastery updates apply to all
  patterns the problem touches.
- Score in [0, 1] derived from (outcome, max_hint_level_used). Hint usage
  is a penalty: solved-with-no-hints is the only way to get 1.0.
"""

from __future__ import annotations

from typing import Iterable

# --- Taxonomy --------------------------------------------------------------

COARSE_PATTERNS: tuple[str, ...] = (
    "arrays",
    "hashing",
    "string",
    "two-pointer",
    "sliding-window",
    "monotonic-stack",
    "binary-search",
    "bfs-dfs",
    "graph",
    "union-find",
    "topological-sort",
    "trie",
    "segment-tree",
    "heap",
    "dp",
    "greedy",
    "backtracking",
    "bit-manip",
    "math",
    "design",
)

# Mapping from raw LeetCode tag (as it appears on /tag/<...>/) to the coarse
# bucket(s) we track. Most tags map to one bucket; a few map to two.
TAG_TO_PATTERNS: dict[str, tuple[str, ...]] = {
    "Array": ("arrays",),
    "Hash Table": ("hashing",),
    "Counting": ("hashing",),
    "String": ("string",),
    "Two Pointers": ("two-pointer",),
    "Sliding Window": ("sliding-window",),
    "Stack": ("monotonic-stack",),
    "Monotonic Stack": ("monotonic-stack",),
    "Queue": ("design",),
    "Monotonic Queue": ("monotonic-stack",),
    "Binary Search": ("binary-search",),
    "Depth-First Search": ("bfs-dfs",),
    "Breadth-First Search": ("bfs-dfs",),
    "Tree": ("graph",),
    "Binary Tree": ("graph",),
    "Binary Search Tree": ("graph",),
    "N-ary Tree": ("graph",),
    "Graph": ("graph",),
    "Union Find": ("union-find",),
    "Topological Sort": ("topological-sort",),
    "Trie": ("trie",),
    "Segment Tree": ("segment-tree",),
    "Binary Indexed Tree": ("segment-tree",),
    "Heap (Priority Queue)": ("heap",),
    "Priority Queue": ("heap",),
    "Dynamic Programming": ("dp",),
    "Memoization": ("dp",),
    "Greedy": ("greedy",),
    "Backtracking": ("backtracking",),
    "Recursion": ("backtracking",),
    "Divide and Conquer": ("backtracking",),
    "Bit Manipulation": ("bit-manip",),
    "Bitmask": ("bit-manip",),
    "Math": ("math",),
    "Number Theory": ("math",),
    "Combinatorics": ("math",),
    "Geometry": ("math",),
    "Design": ("design",),
    "Linked List": ("design",),
    "Ordered Set": ("design",),
    "Doubly-Linked List": ("design",),
    "Database": ("design",),
    "Matrix": ("arrays",),
    "Prefix Sum": ("arrays",),
    "Sorting": ("arrays",),
    "Simulation": ("arrays",),
    "Game Theory": ("dp",),
    "Suffix Array": ("string",),
    "Rolling Hash": ("hashing",),
    "String Matching": ("string",),
    "Iterator": ("design",),
    "Reservoir Sampling": ("math",),
    "Probability and Statistics": ("math",),
    "Concurrency": ("design",),
    "Quickselect": ("arrays",),
    "Bucket Sort": ("arrays",),
    "Counting Sort": ("arrays",),
    "Radix Sort": ("arrays",),
    "Shell": ("design",),
    "Brainteaser": ("math",),
    "Eulerian Circuit": ("graph",),
    "Strongly Connected Component": ("graph",),
    "Biconnected Component": ("graph",),
    "Minimum Spanning Tree": ("graph",),
    "Shortest Path": ("graph",),
    "Hash Function": ("hashing",),
    "Line Sweep": ("arrays",),
    "Interactive": ("design",),
    "Data Stream": ("design",),
    "Randomized": ("math",),
    "Enumeration": ("math",),
    "Number Theory ": ("math",),
}


def map_leetcode_tags_to_patterns(tags: Iterable[str]) -> list[str]:
    """Translate raw LeetCode tag names to coarse pattern names.

    Returns a deduplicated list, preserving discovery order. Unknown tags
    are silently dropped (the problem just maps to fewer patterns).
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        if not raw:
            continue
        for p in TAG_TO_PATTERNS.get(raw.strip(), ()):
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
    return out


# --- Elo math --------------------------------------------------------------

K_FACTOR = 24
INITIAL_PATTERN_ELO = 1200.0


def problem_effective_elo(difficulty: str | None) -> float:
    """Convert LeetCode difficulty to a fixed problem Elo. The pattern Elo
    is what moves; the problem Elo is the opponent's rating in the match."""
    if difficulty is None:
        return 1300.0
    d = difficulty.strip().lower()
    return {"easy": 1100.0, "medium": 1500.0, "hard": 1900.0}.get(d, 1300.0)


def attempt_score(outcome: str, max_hint_level: int) -> float:
    """Map an attempt's (outcome, max hint level used) to an Elo score in [0,1].

    The mapping bakes in the contract: a hint is a partial solution, so
    using a higher hint level reduces the credit you get for solving.
    """
    base = {"solved": 1.0, "partial": 0.5, "stuck": 0.0}.get(outcome.lower())
    if base is None:
        raise ValueError(f"unknown outcome: {outcome!r}")
    if max_hint_level < 0:
        raise ValueError(f"max_hint_level must be >= 0 (got {max_hint_level})")
    penalty = 0.1 * max_hint_level
    return max(0.0, min(1.0, base - penalty))


def expected_score(pattern_elo: float, problem_elo: float) -> float:
    """Standard Elo expected score for the pattern in a 'match' against the
    problem's effective Elo."""
    return 1.0 / (1.0 + 10 ** ((problem_elo - pattern_elo) / 400.0))


def elo_update(
    pattern_elo: float,
    problem_elo: float,
    score: float,
    k: float = K_FACTOR,
) -> float:
    """Return the new pattern Elo after one attempt."""
    e = expected_score(pattern_elo, problem_elo)
    return pattern_elo + k * (score - e)
