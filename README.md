# lc-coach

A personal LeetCode coach that lives inside your browser. Click a hint button on any leetcode.com problem and get a Socratic hint without leaving the page. Track per-pattern mastery (Elo), get spaced-repetition reminders (SM-2), and ask the recommender for a single next problem aimed at any target company you specify.

No API key required — calls go through your existing `claude` CLI subscription via a local Python service.

Status: **v1.0.0** (sessions 1–7).

## Run it

One-time install:
```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Daily:
```bash
./start.sh
```

That boots the local service on `127.0.0.1:8765` and opens a Chrome window with the extension preloaded into a dedicated profile (`~/.lc-coach/chrome-profile`). Sign in to LeetCode in that window once; subsequent launches are zero-config. `Ctrl-C` in the launching terminal stops the service.

If you'd rather use your main Chrome profile: skip the launcher, run `python -m lc_coach` yourself, then `chrome://extensions` → Developer mode → Load unpacked → select `extension/`.

## What's in the side panel

- **Target company** — type any company (autocomplete from your ingested set). Click **Next problem** and the recommender returns a problem with rationale. If the company isn't in the DB yet, the service auto-ingests just that company from public sources (~5–30 s on first hit). For thin targets like SpaceX (15 problems), cold-start expansion automatically draws from similar companies.
- **Weakest patterns** — your bottom-N pattern Elos. Updates after every finished attempt.
- **Due for review** — SM-2 spaced repetition. Click-through opens leetcode.com.
- **Hint buttons** — L1 (pattern category only) → L2 (data structure, no algorithm) → L3 (algorithmic decomposition, no code). Each level has an explicit prompt contract.
- **Review my code** — auto-fetches your Monaco-editor code, returns a staff-engineer-style review (bugs first, then complexity in big-O, then style; never writes the better solution for you).
- **Mock interview** — single-response interview round on the current problem: poses it, lists the clarifying questions you should ask with answers, asks for approach before code, pre-empts the wrong direction, demands complexity commitment, throws a follow-up.
- **Attempt logging** — Start → live timer → I'm done → outcome picker (solved / partial / stuck). Code is auto-captured from Monaco. Every hint you take attaches to the active attempt.

## How it works

```
┌──────────────────────────┐         ┌──────────────────────────┐
│ Chrome MV3 extension     │         │ Local Python service     │
│ — content script scrapes │  HTTP   │ — FastAPI on             │
│   leetcode DOM           │ ──────▶ │   127.0.0.1:8765         │
│ — chrome.scripting       │         │ — SQLite at              │
│   reads Monaco editor    │ ◀────── │   ~/.lc-coach/state.db   │
│ — side panel UI          │         │ — calls claude -p        │
└──────────────────────────┘         │   for LLM responses      │
                                     └──────────────────────────┘
                                                  │
                                                  ▼
                                       ┌─────────────────────┐
                                       │ Public GitHub data   │
                                       │ — snehasishroy 2026  │
                                       │ — liquidslr 2025     │
                                       │ — manual SpaceX seed │
                                       └─────────────────────┘
```

- **Hints are Socratic by contract**, not just by tone. Each level (L1/L2/L3) has explicit forbidden phrasings baked into its prompt template, regression-tested at construction time.
- **Mastery is Elo per coarse pattern** (20 buckets — arrays, hashing, monotonic-stack, dp, etc.). K=24, score = base − 0.1·max_hint_level. Easy/Medium/Hard mapped to 1100/1500/1900 as the "opponent" rating.
- **Spaced repetition is canonical SM-2**. Outcome × max hint level → quality grade q∈{0..5}. q<3 resets the repetition counter and pushes the next review out 1 day.
- **Company similarity = 0.7 · Jaccard(question sets) + 0.3 · cosine(difficulty distribution)**. Cold-start: target with <30 problems triggers expansion to top-k similar companies, scaled by similarity score.
- **No API key.** `lc_coach.coach.claude_p()` is a thin subprocess wrapper around the same `claude` binary you use for Claude Code.

## Stack

Python 3.9, FastAPI, uvicorn, SQLite (stdlib), `claude` CLI subprocess, Chrome MV3 (vanilla JS), Monaco editor introspection via `chrome.scripting` MAIN-world, pytest, Playwright (development verification only).

## Tests

```bash
cd server && .venv/bin/pytest
# 74 passed
```

Coverage: prompt contract regressions, Elo math, SM-2 quality mapping + interval progression, ingest parsing for both source formats, similarity + cold-start expansion + recommender ranking, full attempt lifecycle integration tests with monkeypatched `claude -p`.

## Demo

See [DEMO.md](DEMO.md) for a reproducible end-to-end transcript.

## Roadmap

- ✓ Session 1 — Foundation: extension + service + working hint flow
- ✓ Session 2 — Monaco editor code reading + full attempt logging
- ✓ Session 3 — Per-pattern Elo mastery model
- ✓ Session 4 — Public company-tag ingest + SM-2 spaced repetition
- ✓ Session 5 — Company similarity recommender + target-company workflow
- ✓ Session 6 — Code review + mock-interview modes; prompt contract tests
- ✓ Session 7 — Docs, demo, public ship

Future work I'd consider: lazy LLM-tagging of ingested problems via `claude -p` so the topic-distribution similarity term works densely; multi-turn mock interview state; Chrome Web Store packaging.
