# lc-coach

A personal LeetCode coach that lives inside your browser on leetcode.com. Combines an LLM-driven Socratic hint flow, per-pattern Elo mastery tracking, SM-2 spaced repetition, and a company-similarity recommender that expands thin target-company question pools using profile-matched neighbors.

Architecture: a Chrome extension that injects a side panel into leetcode.com pages, talking to a local Python service that owns state (SQLite) and orchestrates LLM calls by shelling out to the user's existing `claude` CLI. No API keys required.

Built for one user (me), for real interview prep, against a real interview window.

## Why this exists

Public LeetCode question banks tagged by company are thin and noisy — the SpaceX tag in any single aggregator might surface only 30-80 problems, often stale. Practicing only those leaks into memorization, and any single source is biased toward the people who post (often those who failed). Generic "grind 200 mediums" advice ignores both pattern weakness and company profile. And switching between LeetCode and a coaching tool kills the practice habit — context-switch friction becomes the rate limiter.

The coach addresses these at once:

1. **Stays on LeetCode** — extension side panel renders inline next to the problem; hints, recommendations, and progress are visible without leaving the page.
2. **Pattern blindness** — per-pattern Elo mastery model; surfaces weak patterns; biases next-problem selection toward them.
3. **Memorization leakage** — SM-2 spaced repetition surfaces problems again at expanding intervals so you re-derive instead of recognize.
4. **Thin company data** — each company modeled as (topic distribution × difficulty distribution × recency-weighted question set); recommender expands a thin target-company pool to its top-N similar companies. SpaceX's small public pool becomes a robust ~5x-larger pool of profile-matched problems.
5. **Socratic by default** — graduated hints (level 1: pattern category; level 2: data structure; level 3: subproblem decomposition); never dumps solutions unprompted.

## Goals

- A daily-driver browser extension usable end-of-day-1: open any LeetCode problem, click a hint button, get a Socratic hint rendered in the side panel.
- By end-of-week-1: pattern mastery model, spaced repetition, company-similarity recommender, target-company workflow keyed on SpaceX as the seed user case, code-review and mock-interview modes.
- Honest data quality: multi-source intersection, recency weighting, confidence scoring per (company, problem) row.
- Resume-grade: real recommender + real mastery model + real LLM agent integration + real browser extension. No toys.

## Non-goals

- Multi-user. One user, one local service, one SQLite file.
- Cross-browser parity. Chrome (and Chromium-based browsers like Edge/Brave) only for v1. Firefox if it's free.
- Cloud-hosted service. Local-only, 127.0.0.1 binding only.
- Scraping LeetCode directly outside the user's own session (ToS). Read DOM in the user's tab only; question banks come from public GitHub aggregators.
- Behavioral / system-design / role-specific prep — out of scope; coach handles algorithmic only.
- Anything cleverer than cosine similarity for company profiles in v1. No embeddings, no clustering, no PCA.
- Anthropic API integration. Calls go through the local `claude` CLI subscription instead.

## Stack

**Local service**
- Python 3.9+
- FastAPI + uvicorn — HTTP layer
- SQLite (stdlib `sqlite3`) — state: problems, attempts, hints, mastery, schedule, companies, company_problems
- `subprocess` shell-out to `claude -p` — LLM calls (uses Claude Code subscription; no API key)
- `requests` — fetch public company-tag GitHub repos (one-time ingest)
- `pytest` — unit + integration tests

**Browser extension**
- Manifest V3, Chrome 114+ (for `sidePanel` API)
- Vanilla JS / HTML / CSS — no framework for v1; keep build-step-free
- Content script for DOM scraping (problem text + Monaco editor code)
- Background service worker for messaging
- `fetch` to `http://127.0.0.1:8765` for service calls

## Scope

1 week, 7 sessions, ~20-25 hours. Working state after every session. Day 1 lands a real hint flow on a real problem; subsequent days layer intelligence (mastery → ingest+spaced rep → similarity recommender → coach polish → ship).

## Target resume bullets

- Built a Chrome extension + local Python service for personalized LeetCode practice; integrates inline with leetcode.com via DOM scraping and Monaco editor hooks, with LLM-driven Socratic hints orchestrated through a local `claude` CLI subprocess (no third-party API key).
- Per-pattern Elo mastery model + SM-2 spaced repetition; daily-driver tool that drove [measure: hard solve rate from X% to Y% over Z months].
- Modeled ~50 tech companies as topic/difficulty distributions over their public interview question sets; built a similarity-based recommender that expands a target-company practice pool ~5x while preserving question profile, with cold-start fallback for thin companies.

## Risks / unknowns

- **DOM/Monaco fragility.** LeetCode's React UI changes; selectors and Monaco hooks can break. Mitigation: defensive selectors, retry with timeout, fail loudly in side panel rather than silently.
- **`claude -p` latency.** 3-10s per call. Mitigation: spinner, optimistic UI, no auto-fire on every keystroke.
- **`claude -p` reliability.** Subprocess can fail (auth expired, network blip). Mitigation: clear error surface in side panel with a retry button.
- **Data freshness.** Public company-tag repos are 2019-2024 vintage. Mitigation: multi-source intersection + exponential recency decay.
- **SpaceX cold start.** Thin coverage in public aggregators. Mitigation: cold-start fallback baked into recommender — any company with <100 high-confidence problems auto-expands to similar-companies union.
- **Coach-instead-of-grind trap.** Risk that this project eats prep time. Mitigation: v0.5 usable end-of-day-1; from day 2 onward, daily practice + tool refinement, not only tool-building.
- **Prompt drift.** Hints can drift to generic. Mitigation: versioned prompt templates with explicit hint-level contracts + golden-output tests on a small fixture set.
- **Chrome Web Store gating.** Skipping the store; "Load unpacked" only for v1. Acceptable for personal-use tool.

## Layout (planned)

```
lc-coach/
├── PROJECT.md
├── SESSIONS.md
├── README.md
├── DEMO.md
├── server/
│   ├── pyproject.toml
│   ├── lc_coach/
│   │   ├── __init__.py
│   │   ├── app.py            # FastAPI app + uvicorn entrypoint
│   │   ├── db.py             # SQLite schema + helpers
│   │   ├── coach.py          # `claude -p` shell-out + prompt assembly
│   │   ├── ingest.py         # public company-tag repo fetch + normalize
│   │   ├── mastery.py        # Elo per-pattern model
│   │   ├── schedule.py       # SM-2 spaced repetition
│   │   ├── recommend.py      # similarity + next-problem selection
│   │   └── prompts/
│   │       ├── hint_level_1.txt
│   │       ├── hint_level_2.txt
│   │       ├── hint_level_3.txt
│   │       ├── code_review.txt
│   │       └── mock_interview.txt
│   └── tests/
└── extension/
    ├── manifest.json
    ├── background.js          # service worker
    ├── content.js             # injected into leetcode.com
    ├── sidepanel.html
    ├── sidepanel.js
    ├── sidepanel.css
    └── icons/
```
