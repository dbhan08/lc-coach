"""Hand-curated SpaceX seed list.

Public company-tag aggregators have effectively zero SpaceX coverage (the
biggest one with SpaceX, snehasishroy 2026-02, lists exactly one problem).
Even adjacent defense/aerospace companies — Blue Origin, Boeing — have
fewer than 5 problems each. So the data layer alone gives the recommender
nothing to work with for a SpaceX-targeted user.

This module fills the gap with a small, honestly-labeled seed list drawn
from public interview-prep guides and Glassdoor-style reports describing
SpaceX's interview style:

- LeetCode mediums on hashing / binary search / arrays / stacks / strings
- Linked lists (cycle detection, reversal) and trees (traversal, BST ops)
- Threading-flavored design problems (LRU cache, producer/consumer)
- The one Minimum-Path-Sum hit that the dataset has

Confidence is intentionally lower than a multi-source aggregator hit.
This is "an informed guess at SpaceX's flavor," not "what they asked
last week." The recommender weights it accordingly.

Sources of judgment (cited in commit, not consumed at runtime):
- interviewing.io/spacex-interview-questions (topic list)
- interviewquery.com SpaceX guide
- Glassdoor SpaceX SWE interview reports
- TeamBlind SpaceX threads
- snehasishroy 2026-02 dataset (one entry, kept)
"""

from __future__ import annotations

# Each entry: (slug, title, leetcode_id, difficulty, rationale)
# Rationale stays in code so we can audit later.
SPACEX_SEED: list[tuple[str, str, int, str, str]] = [
    (
        "minimum-path-sum",
        "Minimum Path Sum",
        64,
        "Medium",
        "the one entry public aggregators actually have for SpaceX",
    ),
    (
        "two-sum",
        "Two Sum",
        1,
        "Easy",
        "hash-map staple; SpaceX guide cites hashing as a focus topic",
    ),
    (
        "linked-list-cycle",
        "Linked List Cycle",
        141,
        "Easy",
        "Glassdoor: 'find a cycle in a singly-linked list' explicitly cited",
    ),
    (
        "reverse-linked-list",
        "Reverse Linked List",
        206,
        "Easy",
        "linked-list manipulation foundation; ubiquitous in systems-team interviews",
    ),
    (
        "lru-cache",
        "LRU Cache",
        146,
        "Medium",
        "design + threading-aware data structure; SpaceX system-design overlap",
    ),
    (
        "binary-search",
        "Binary Search",
        704,
        "Easy",
        "binary search is on the topic list",
    ),
    (
        "valid-parentheses",
        "Valid Parentheses",
        20,
        "Easy",
        "stack staple; SpaceX guide cites stacks as a focus topic",
    ),
    (
        "merge-intervals",
        "Merge Intervals",
        56,
        "Medium",
        "sorting + sweep; SpaceX guide cites sorting as a focus topic",
    ),
    (
        "maximum-subarray",
        "Maximum Subarray",
        53,
        "Medium",
        "Kadane's; arrays/DP overlap with SpaceX style",
    ),
    (
        "number-of-islands",
        "Number of Islands",
        200,
        "Medium",
        "BFS/DFS on grid; common in SWE-systems screens",
    ),
    (
        "trapping-rain-water",
        "Trapping Rain Water",
        42,
        "Hard",
        "two-pointer/monotonic-stack; signature 'hybrid' difficulty",
    ),
    (
        "binary-tree-inorder-traversal",
        "Binary Tree Inorder Traversal",
        94,
        "Easy",
        "tree foundation; reported in Glassdoor SpaceX threads",
    ),
    (
        "lowest-common-ancestor-of-a-binary-tree",
        "Lowest Common Ancestor of a Binary Tree",
        236,
        "Medium",
        "tree problem with subtle correctness; matches SpaceX 'thinking' framing",
    ),
    (
        "design-hashmap",
        "Design HashMap",
        706,
        "Easy",
        "Glassdoor: 'hash table collision handling' explicitly cited",
    ),
    (
        "design-circular-queue",
        "Design Circular Queue",
        622,
        "Medium",
        "approximates the 'thread-safe queue design' Glassdoor reports",
    ),
]


def as_records() -> list[dict]:
    """Return the seed as plain dicts so the ingest layer can treat it like
    any other source."""
    return [
        {
            "slug": slug,
            "title": title,
            "leetcode_id": lc_id,
            "difficulty": diff,
            "rationale": rationale,
        }
        for slug, title, lc_id, diff, rationale in SPACEX_SEED
    ]
