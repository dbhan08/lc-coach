"""Public company-tag ingest.

Two GitHub-hosted CSV aggregators + one hand-curated SpaceX seed:

- **snehasishroy/leetcode-companywise-interview-questions** (Feb 2026):
  primary; per-company `<window>.csv` with columns ID, URL, Title,
  Difficulty, Acceptance %, Frequency %. Default branch `master`.
  Folder names are lowercase + hyphens (e.g. `palantir-technologies`).
- **liquidslr/interview-company-wise-problems** (Jun 2025): secondary;
  per-company `<n>. <Window Name>.csv` with columns Difficulty, Title,
  Frequency, Acceptance Rate, Link. Default branch `main`. Folder names
  are CamelCase + spaces (e.g. `Palantir Technologies`).
- **lc_coach.spacex_seed**: ~15 hand-curated SpaceX problems with low
  confidence weight, drawn from public interview guides.

Aggregation:
- Normalize company names to a single canonical form (lowercase + hyphen).
- Normalize windows to {30d, 3mo, 6mo, older, all} with recency weights.
- Per (company, slug) accumulate sources and windows; final confidence =
  sum over (source × window) appearances.
- Cache raw CSVs on disk so reruns don't re-fetch.

The fetcher is injectable so tests substitute fixtures instead of hitting
GitHub.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

USER_AGENT = "lc-coach/0.1 (+https://github.com/dbhan08/lc-coach)"
DEFAULT_CACHE_DIR = Path.home() / ".lc-coach" / "companies-raw"

# Canonical window names → recency weight
WINDOW_WEIGHTS: dict[str, float] = {
    "30d": 1.0,
    "3mo": 0.7,
    "6mo": 0.5,
    "older": 0.3,
    "all": 0.4,
}

# Source identifiers and their global weight multipliers
SOURCE_WEIGHTS: dict[str, float] = {
    "snehasishroy_2026-02": 1.0,
    "liquidslr_2025-06": 0.9,
    "manual_spacex_2026-05": 0.5,
}


# --- Data shapes ----------------------------------------------------------


@dataclass
class CompanyProblemRow:
    company: str  # canonical name
    slug: str
    leetcode_id: Optional[int] = None
    title: str = ""
    difficulty: Optional[str] = None
    # set of (source_id, window) tuples — order doesn't matter
    appearances: set[tuple[str, str]] = field(default_factory=set)
    confidence: float = 0.0


def normalize_company(name: str) -> str:
    """Canonical form: lowercase, single-hyphen-separated, alnum + hyphen."""
    name = (name or "").strip().lower()
    # collapse whitespace + underscores + repeated punctuation to single hyphens
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name


_SLUG_FROM_URL = re.compile(r"/problems/([^/?#\s]+)")


def slug_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = _SLUG_FROM_URL.search(url)
    return m.group(1).strip().lower() if m else None


# --- Fetcher abstraction --------------------------------------------------


class HttpClient:
    def __init__(self, cache_dir: Path = DEFAULT_CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, url: str, *, cache_key: Optional[str] = None) -> Optional[str]:
        if cache_key:
            cached = self.cache_dir / cache_key
            if cached.exists():
                return cached.read_text(encoding="utf-8", errors="replace")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
        if cache_key:
            cached = self.cache_dir / cache_key
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, encoding="utf-8")
        return text


class Source:
    """Abstract source. A source knows: its id, weight, how to enumerate
    company directory names, how to fetch per-window CSVs, and how to
    parse them into normalized row records."""

    id: str = ""
    weight: float = 1.0

    def list_company_dirs(self, http: HttpClient) -> list[str]:
        raise NotImplementedError

    def fetch_window(
        self, http: HttpClient, company_dir: str, window: str
    ) -> Optional[str]:
        raise NotImplementedError

    def parse_csv(self, text: str) -> list[dict]:
        raise NotImplementedError

    def windows(self) -> list[str]:
        return ["30d", "3mo", "6mo", "older", "all"]


# --- snehasishroy --------------------------------------------------------


_SNEHA_WINDOW_FILE = {
    "30d": "thirty-days.csv",
    "3mo": "three-months.csv",
    "6mo": "six-months.csv",
    "older": "more-than-six-months.csv",
    "all": "all.csv",
}


class SnehasishroySource(Source):
    id = "snehasishroy_2026-02"
    weight = SOURCE_WEIGHTS[id]
    OWNER = "snehasishroy"
    REPO = "leetcode-companywise-interview-questions"
    BRANCH = "master"

    def list_company_dirs(self, http: HttpClient) -> list[str]:
        url = (
            f"https://api.github.com/repos/{self.OWNER}/{self.REPO}"
            f"/contents/?per_page=2000"
        )
        body = http.get(url, cache_key=f"{self.id}/_index.json")
        if not body:
            return []
        data = json.loads(body)
        return [item["name"] for item in data if item.get("type") == "dir"]

    def fetch_window(
        self, http: HttpClient, company_dir: str, window: str
    ) -> Optional[str]:
        fname = _SNEHA_WINDOW_FILE.get(window)
        if not fname:
            return None
        url = (
            f"https://raw.githubusercontent.com/{self.OWNER}/{self.REPO}"
            f"/{self.BRANCH}/{urllib.parse.quote(company_dir)}/{fname}"
        )
        return http.get(url, cache_key=f"{self.id}/{company_dir}/{fname}")

    def parse_csv(self, text: str) -> list[dict]:
        if not text:
            return []
        out: list[dict] = []
        try:
            reader = csv.DictReader(io.StringIO(text))
        except csv.Error:
            return []
        for row in reader:
            link = (row.get("URL") or "").strip()
            slug = slug_from_url(link)
            if not slug:
                continue
            try:
                lc_id = int((row.get("ID") or "").strip())
            except ValueError:
                lc_id = None
            out.append(
                {
                    "slug": slug,
                    "leetcode_id": lc_id,
                    "title": (row.get("Title") or "").strip(),
                    "difficulty": (row.get("Difficulty") or "").strip() or None,
                }
            )
        return out


# --- liquidslr -----------------------------------------------------------


_LIQUID_WINDOW_FILE = {
    "30d": "1. Thirty Days.csv",
    "3mo": "2. Three Months.csv",
    "6mo": "3. Six Months.csv",
    "older": "4. More Than Six Months.csv",
    "all": "5. All.csv",
}


class LiquidslrSource(Source):
    id = "liquidslr_2025-06"
    weight = SOURCE_WEIGHTS[id]
    OWNER = "liquidslr"
    REPO = "interview-company-wise-problems"
    BRANCH = "main"

    def list_company_dirs(self, http: HttpClient) -> list[str]:
        url = (
            f"https://api.github.com/repos/{self.OWNER}/{self.REPO}"
            f"/contents/?per_page=2000"
        )
        body = http.get(url, cache_key=f"{self.id}/_index.json")
        if not body:
            return []
        data = json.loads(body)
        return [item["name"] for item in data if item.get("type") == "dir"]

    def fetch_window(
        self, http: HttpClient, company_dir: str, window: str
    ) -> Optional[str]:
        fname = _LIQUID_WINDOW_FILE.get(window)
        if not fname:
            return None
        url = (
            f"https://raw.githubusercontent.com/{self.OWNER}/{self.REPO}"
            f"/{self.BRANCH}/{urllib.parse.quote(company_dir)}"
            f"/{urllib.parse.quote(fname)}"
        )
        return http.get(
            url, cache_key=f"{self.id}/{company_dir}/{fname.replace(' ', '_')}"
        )

    def parse_csv(self, text: str) -> list[dict]:
        if not text:
            return []
        out: list[dict] = []
        try:
            reader = csv.DictReader(io.StringIO(text))
        except csv.Error:
            return []
        for row in reader:
            link = (row.get("Link") or "").strip()
            slug = slug_from_url(link)
            if not slug:
                continue
            out.append(
                {
                    "slug": slug,
                    "leetcode_id": None,  # liquidslr's CSVs don't include the LC numeric id
                    "title": (row.get("Title") or "").strip(),
                    "difficulty": (row.get("Difficulty") or "").strip() or None,
                }
            )
        return out


# --- manual SpaceX seed --------------------------------------------------


class ManualSpaceXSource(Source):
    id = "manual_spacex_2026-05"
    weight = SOURCE_WEIGHTS[id]

    def list_company_dirs(self, http: HttpClient) -> list[str]:
        return ["spacex"]

    def fetch_window(
        self, http: HttpClient, company_dir: str, window: str
    ) -> Optional[str]:
        # Only emit the seed once, under "all"; signal "no data" for the
        # time-windowed slots so we don't double-count.
        if company_dir != "spacex" or window != "all":
            return None
        return "__manual_spacex__"

    def parse_csv(self, text: str) -> list[dict]:
        if text != "__manual_spacex__":
            return []
        from lc_coach.spacex_seed import as_records

        return as_records()


# --- Aggregation ---------------------------------------------------------


def aggregate(
    sources: Iterable[Source],
    *,
    http: Optional[HttpClient] = None,
    companies_filter: Optional[Iterable[str]] = None,
    progress: Optional[Callable[[str, str], None]] = None,
) -> dict[str, dict[str, CompanyProblemRow]]:
    """Run the full aggregation pipeline.

    Returns: {canonical_company_name: {slug: CompanyProblemRow}}
    """
    http = http or HttpClient()
    filter_set = (
        {normalize_company(c) for c in companies_filter}
        if companies_filter is not None
        else None
    )

    out: dict[str, dict[str, CompanyProblemRow]] = {}

    for source in sources:
        try:
            company_dirs = source.list_company_dirs(http)
        except Exception as exc:
            if progress:
                progress(source.id, f"(failed: {exc})")
            continue

        for cdir in company_dirs:
            canonical = normalize_company(cdir)
            if not canonical:
                continue
            if filter_set is not None and canonical not in filter_set:
                continue
            if progress:
                progress(source.id, canonical)

            company_rows = out.setdefault(canonical, {})

            for window in source.windows():
                csv_text = source.fetch_window(http, cdir, window)
                if csv_text is None:
                    continue
                for entry in source.parse_csv(csv_text):
                    slug = entry["slug"]
                    row = company_rows.get(slug)
                    if row is None:
                        row = CompanyProblemRow(company=canonical, slug=slug)
                        company_rows[slug] = row
                    if not row.title and entry.get("title"):
                        row.title = entry["title"]
                    if not row.difficulty and entry.get("difficulty"):
                        row.difficulty = entry["difficulty"]
                    if row.leetcode_id is None and entry.get("leetcode_id"):
                        row.leetcode_id = entry["leetcode_id"]
                    row.appearances.add((source.id, window))

    # Compute confidence
    for company_rows in out.values():
        for row in company_rows.values():
            row.confidence = sum(
                SOURCE_WEIGHTS.get(sid, 1.0) * WINDOW_WEIGHTS.get(win, 0.0)
                for sid, win in row.appearances
            )

    # Drop empty companies
    return {c: rows for c, rows in out.items() if rows}


def default_sources() -> list[Source]:
    return [SnehasishroySource(), LiquidslrSource(), ManualSpaceXSource()]
