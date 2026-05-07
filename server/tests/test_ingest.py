from typing import Optional

import pytest

from lc_coach import db
from lc_coach.ingest import (
    LiquidslrSource,
    ManualSpaceXSource,
    SnehasishroySource,
    aggregate,
    normalize_company,
    slug_from_url,
)


def test_normalize_company():
    assert normalize_company("Apple") == "apple"
    assert normalize_company("Palantir Technologies") == "palantir-technologies"
    assert normalize_company("two_sigma") == "two-sigma"
    assert normalize_company("BLUE-Origin") == "blue-origin"
    assert normalize_company("  weird  whitespace  ") == "weird-whitespace"
    assert normalize_company("Citadel, LLC") == "citadel-llc"


def test_slug_from_url():
    assert slug_from_url("https://leetcode.com/problems/two-sum") == "two-sum"
    assert slug_from_url("https://leetcode.com/problems/two-sum/") == "two-sum"
    assert (
        slug_from_url("https://leetcode.com/problems/lru-cache?tab=description")
        == "lru-cache"
    )
    assert slug_from_url("") is None
    assert slug_from_url("https://example.com/something") is None


def test_snehasishroy_parser_real_format():
    text = (
        "ID,URL,Title,Difficulty,Acceptance %,Frequency %\n"
        "1,https://leetcode.com/problems/two-sum,Two Sum,Easy,55.0%,100.0%\n"
        "146,https://leetcode.com/problems/lru-cache,LRU Cache,Medium,42.0%,80.0%\n"
    )
    rows = SnehasishroySource().parse_csv(text)
    assert [r["slug"] for r in rows] == ["two-sum", "lru-cache"]
    assert rows[0]["leetcode_id"] == 1
    assert rows[0]["difficulty"] == "Easy"
    assert rows[1]["title"] == "LRU Cache"


def test_liquidslr_parser_real_format():
    text = (
        "Difficulty,Title,Frequency,Acceptance Rate,Link\n"
        "Easy,Two Sum,100%,55.0%,https://leetcode.com/problems/two-sum\n"
        "Medium,LRU Cache,80%,42.0%,https://leetcode.com/problems/lru-cache/\n"
    )
    rows = LiquidslrSource().parse_csv(text)
    assert [r["slug"] for r in rows] == ["two-sum", "lru-cache"]
    assert rows[0]["leetcode_id"] is None  # liquidslr doesn't include ID
    assert rows[0]["title"] == "Two Sum"
    assert rows[0]["difficulty"] == "Easy"


def test_manual_spacex_source_emits_seed():
    src = ManualSpaceXSource()
    assert src.list_company_dirs(http=None) == ["spacex"]
    text = src.fetch_window(http=None, company_dir="spacex", window="all")
    rows = src.parse_csv(text)
    assert len(rows) >= 10
    # Two Sum and LRU Cache are explicitly in the seed
    slugs = [r["slug"] for r in rows]
    assert "two-sum" in slugs
    assert "lru-cache" in slugs

    # Other windows should produce nothing — we don't want to double-count
    for win in ("30d", "3mo", "6mo", "older"):
        assert src.fetch_window(http=None, company_dir="spacex", window=win) is None


# ---- Aggregation ---------------------------------------------------------


class FakeSource:
    """Minimal fake source for testing aggregate()."""

    def __init__(self, source_id: str, weight: float, companies: dict):
        # companies = {company_dir: {window: [{slug, title, difficulty, leetcode_id}, ...]}}
        self.id = source_id
        self.weight = weight
        self._companies = companies

    def list_company_dirs(self, http) -> list[str]:
        return list(self._companies.keys())

    def fetch_window(self, http, company_dir: str, window: str) -> Optional[str]:
        windows = self._companies.get(company_dir, {})
        if window not in windows:
            return None
        return f"FAKE:{company_dir}:{window}"

    def parse_csv(self, text: str) -> list[dict]:
        if not text.startswith("FAKE:"):
            return []
        _, company_dir, window = text.split(":", 2)
        return list(self._companies.get(company_dir, {}).get(window, []))

    def windows(self) -> list[str]:
        return ["30d", "3mo", "6mo", "older", "all"]


def test_aggregate_combines_two_sources_and_dedupes():
    # Two sources both saying apple has two-sum, in different windows
    src_a = FakeSource(
        "snehasishroy_2026-02",
        1.0,
        {
            "apple": {
                "30d": [
                    {"slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "leetcode_id": 1}
                ],
                "all": [
                    {"slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "leetcode_id": 1}
                ],
            }
        },
    )
    src_b = FakeSource(
        "liquidslr_2025-06",
        0.9,
        {
            "Apple": {
                "6mo": [
                    {"slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "leetcode_id": None}
                ],
            }
        },
    )

    out = aggregate([src_a, src_b])
    assert "apple" in out  # both src_a's "apple" and src_b's "Apple" canonicalize the same way
    rows = out["apple"]
    assert "two-sum" in rows
    appearances = rows["two-sum"].appearances
    assert (src_a.id, "30d") in appearances
    assert (src_a.id, "all") in appearances
    assert (src_b.id, "6mo") in appearances
    # Confidence > 0 and reflects all 3 appearances
    assert rows["two-sum"].confidence > 0


def test_aggregate_filter_companies_works():
    src = FakeSource(
        "snehasishroy_2026-02",
        1.0,
        {
            "apple": {"all": [{"slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "leetcode_id": 1}]},
            "tesla": {"all": [{"slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "leetcode_id": 1}]},
            "amazon": {"all": [{"slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "leetcode_id": 1}]},
        },
    )
    out = aggregate([src], companies_filter=["apple", "tesla"])
    assert set(out.keys()) == {"apple", "tesla"}


def test_store_aggregated_companies_round_trip(tmp_db):
    db.init_db()
    src = FakeSource(
        "snehasishroy_2026-02",
        1.0,
        {
            "apple": {
                "30d": [
                    {"slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "leetcode_id": 1}
                ],
                "all": [
                    {"slug": "two-sum", "title": "Two Sum", "difficulty": "Easy", "leetcode_id": 1},
                    {"slug": "lru-cache", "title": "LRU Cache", "difficulty": "Medium", "leetcode_id": 146},
                ],
            }
        },
    )
    aggregated = aggregate([src])
    with db.connect() as conn:
        counts = db.store_aggregated_companies(conn, aggregated)
    assert counts == {"companies": 1, "problems": 2}

    with db.connect() as conn:
        companies = db.get_companies_with_counts(conn)
        problems = db.get_company_problems(conn, "apple")
    assert len(companies) == 1
    assert companies[0]["name"] == "apple"
    assert companies[0]["n_problems"] == 2
    slugs = {p["problem_slug"] for p in problems}
    assert slugs == {"two-sum", "lru-cache"}
    # Highest-confidence row is two-sum (it appeared in both 30d and all)
    assert problems[0]["problem_slug"] == "two-sum"
