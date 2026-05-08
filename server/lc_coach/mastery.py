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


# Heuristic slug → pattern mapping. Used by the ingest pipeline to assign
# coarse patterns to problems we haven't yet opened (and therefore haven't
# pulled real LeetCode tags for via `/problems`). Keys are substrings; the
# first match wins, but multiple keys can match a slug. Lossy but better
# than zero coverage when populating problem_patterns at ingest time.
SLUG_KEYWORDS_TO_PATTERNS: dict[str, str] = {
    # union-find — check first (some slugs match other patterns too)
    "redundant-connection": "union-find",
    "accounts-merge": "union-find",
    "number-of-provinces": "union-find",
    "graph-valid-tree": "union-find",
    "regions-cut-by-slashes": "union-find",
    # topological sort
    "course-schedule": "topological-sort",
    "alien-dictionary": "topological-sort",
    "topological": "topological-sort",
    "build-a-matrix": "topological-sort",
    # trie
    "trie": "trie",
    "prefix-tree": "trie",
    "word-search-ii": "trie",
    "implement-magic-dictionary": "trie",
    "stream-of-characters": "trie",
    "search-suggestions": "trie",
    # segment tree
    "segment-tree": "segment-tree",
    "range-sum-query-mutable": "segment-tree",
    "count-of-smaller-numbers": "segment-tree",
    # heap
    "kth-largest": "heap",
    "kth-smallest": "heap",
    "top-k": "heap",
    "find-median-from-data-stream": "heap",
    "merge-k-sorted": "heap",
    "task-scheduler": "heap",
    "k-closest-points": "heap",
    # monotonic stack / stack
    "trapping-rain-water": "monotonic-stack",
    "largest-rectangle": "monotonic-stack",
    "next-greater-element": "monotonic-stack",
    "daily-temperatures": "monotonic-stack",
    "monotonic": "monotonic-stack",
    "valid-parentheses": "monotonic-stack",
    "min-stack": "monotonic-stack",
    "evaluate-reverse-polish": "monotonic-stack",
    "decode-string": "monotonic-stack",
    # sliding window
    "longest-substring-without-repeat": "sliding-window",
    "minimum-window-substring": "sliding-window",
    "sliding-window": "sliding-window",
    "find-all-anagrams": "sliding-window",
    "substring-with-concatenation": "sliding-window",
    "longest-repeating-character": "sliding-window",
    "permutation-in-string": "sliding-window",
    # two pointer
    "two-pointers": "two-pointer",
    "container-with-most-water": "two-pointer",
    "remove-duplicates-from-sorted-array": "two-pointer",
    "3sum": "two-pointer",
    "4sum": "two-pointer",
    "linked-list-cycle": "two-pointer",
    "middle-of-the-linked-list": "two-pointer",
    "happy-number": "two-pointer",
    "remove-nth-node": "two-pointer",
    "palindrome-linked-list": "two-pointer",
    "reorder-list": "two-pointer",
    # binary search
    "binary-search": "binary-search",
    "search-in-rotated": "binary-search",
    "find-peak": "binary-search",
    "median-of-two-sorted-arrays": "binary-search",
    "find-first-and-last-position": "binary-search",
    "koko-eating-bananas": "binary-search",
    "search-insert-position": "binary-search",
    "search-2d-matrix": "binary-search",
    "split-array-largest-sum": "binary-search",
    # graph (incl. trees — we bucket trees under graph)
    "binary-tree": "graph",
    "binary-search-tree": "graph",
    "lowest-common-ancestor": "graph",
    "validate-bst": "graph",
    "kth-smallest-element-in-a-bst": "graph",
    "n-ary-tree": "graph",
    "tree-traversal": "graph",
    "serialize-and-deserialize": "graph",
    "diameter-of-binary-tree": "graph",
    "path-sum": "graph",
    "shortest-path": "graph",
    "network-delay-time": "graph",
    "evaluate-division": "graph",
    "cheapest-flights": "graph",
    "min-cost-to-connect": "graph",
    "swim-in-rising-water": "graph",
    "is-graph-bipartite": "graph",
    "minimum-genetic-mutation": "graph",
    "redundant-connection-ii": "graph",
    "reconstruct-itinerary": "graph",
    "find-eventual-safe-states": "graph",
    "tree": "graph",
    # bfs / dfs (grid + general)
    "number-of-islands": "bfs-dfs",
    "rotting-oranges": "bfs-dfs",
    "word-ladder": "bfs-dfs",
    "open-the-lock": "bfs-dfs",
    "max-area-of-island": "bfs-dfs",
    "flood-fill": "bfs-dfs",
    "pacific-atlantic": "bfs-dfs",
    "surrounded-regions": "bfs-dfs",
    "walls-and-gates": "bfs-dfs",
    "01-matrix": "bfs-dfs",
    "shortest-bridge": "bfs-dfs",
    "as-far-from-land-as-possible": "bfs-dfs",
    "snake-and-ladders": "bfs-dfs",
    # dp
    "longest-increasing-subsequence": "dp",
    "longest-common-subsequence": "dp",
    "longest-palindromic-substring": "dp",
    "edit-distance": "dp",
    "coin-change": "dp",
    "house-robber": "dp",
    "climbing-stairs": "dp",
    "fibonacci": "dp",
    "unique-paths": "dp",
    "minimum-path-sum": "dp",
    "word-break": "dp",
    "decode-ways": "dp",
    "regular-expression-matching": "dp",
    "wildcard-matching": "dp",
    "interleaving-string": "dp",
    "distinct-subsequences": "dp",
    "best-time-to-buy-and-sell-stock-iv": "dp",
    "burst-balloons": "dp",
    "scramble-string": "dp",
    "maximum-product-subarray": "dp",
    "maximum-subarray": "dp",  # Kadane's
    "partition-equal-subset-sum": "dp",
    "target-sum": "dp",
    "stone-game": "dp",
    "palindrome-partitioning-ii": "dp",
    "perfect-squares": "dp",
    # greedy
    "jump-game": "greedy",
    "gas-station": "greedy",
    "best-time-to-buy-and-sell-stock": "greedy",
    "candy": "greedy",
    "minimum-number-of-arrows": "greedy",
    "non-overlapping-intervals": "greedy",
    "queue-reconstruction": "greedy",
    "minimum-rounds-to-complete-all-tasks": "greedy",
    # backtracking
    "combinations": "backtracking",
    "combination-sum": "backtracking",
    "permutations": "backtracking",
    "subsets": "backtracking",
    "n-queens": "backtracking",
    "sudoku-solver": "backtracking",
    "word-search": "backtracking",
    "letter-combinations": "backtracking",
    "palindrome-partitioning": "backtracking",
    "restore-ip-addresses": "backtracking",
    "generate-parentheses": "backtracking",
    "expression-add-operators": "backtracking",
    # bit manipulation
    "single-number": "bit-manip",
    "number-of-1-bits": "bit-manip",
    "counting-bits": "bit-manip",
    "missing-number": "bit-manip",
    "reverse-bits": "bit-manip",
    "sum-of-two-integers": "bit-manip",
    "bit": "bit-manip",
    "xor": "bit-manip",
    # math
    "happy-number": "math",
    "reverse-integer": "math",
    "palindrome-number": "math",
    "string-to-integer": "math",
    "pow-x-n": "math",
    "sqrt": "math",
    "factorial": "math",
    "excel-sheet-column": "math",
    "rotate-image": "math",
    "spiral-matrix": "math",
    "fraction-to-recurring": "math",
    "perfect-number": "math",
    # design / linked list
    "lru-cache": "design",
    "lfu-cache": "design",
    "design-": "design",
    "implement-trie": "trie",
    "implement-queue": "design",
    "implement-stack": "design",
    "linked-list": "design",
    "merge-two-sorted-lists": "design",
    "merge-k-sorted-lists": "heap",
    "reverse-linked-list": "design",
    "rotate-list": "design",
    "copy-list-with-random": "design",
    "add-two-numbers": "design",
    # hashing
    "two-sum": "hashing",
    "group-anagrams": "hashing",
    "valid-anagram": "hashing",
    "contains-duplicate": "hashing",
    "isomorphic-strings": "hashing",
    "ransom-note": "hashing",
    "longest-consecutive-sequence": "hashing",
    "first-unique-character": "hashing",
    "intersection-of-two-arrays": "hashing",
    "subarray-sum-equals-k": "hashing",
    "find-all-duplicates": "hashing",
    "design-hashmap": "hashing",
    "design-hashset": "hashing",
    "anagram": "hashing",
    "fizz-buzz": "hashing",
    # string
    "longest-common-prefix": "string",
    "string-rotation": "string",
    "valid-palindrome": "string",
    "longest-palindrome": "string",
    "compare-version-numbers": "string",
    "zigzag-conversion": "string",
    "minimum-genetic-string": "string",
    "string-compression": "string",
    "encode-and-decode-strings": "string",
    "find-the-difference": "string",
    "first-bad-version": "binary-search",
    "valid-number": "string",
    "string-multiplication": "string",
    # arrays (last so other patterns win)
    "rotate-array": "arrays",
    "majority-element": "arrays",
    "move-zeroes": "arrays",
    "merge-sorted-array": "arrays",
    "remove-element": "arrays",
    "best-time-to-buy-and-sell-stock-with-cooldown": "dp",
    "product-of-array-except-self": "arrays",
    "summary-ranges": "arrays",
    "merge-intervals": "arrays",
    "insert-interval": "arrays",
    "meeting-rooms": "arrays",
    "shuffle-an-array": "arrays",
    "find-pivot-index": "arrays",
    "running-sum": "arrays",
    "increasing-triplet": "arrays",
    "median-of-data-stream": "heap",
    "sort-": "arrays",
    "max-consecutive-ones": "arrays",
    "find-the-celebrity": "arrays",
    "third-maximum-number": "arrays",
}


def infer_patterns_from_slug(slug: str) -> list[str]:
    """Heuristic slug → coarse patterns. Returns deduplicated list (most-specific
    matches first since SLUG_KEYWORDS_TO_PATTERNS is roughly ordered that way)."""
    s = (slug or "").strip().lower()
    if not s:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for keyword, pattern in SLUG_KEYWORDS_TO_PATTERNS.items():
        if keyword in s and pattern not in seen:
            seen.add(pattern)
            out.append(pattern)
    return out


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
