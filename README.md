# lc-coach

A personal LeetCode coach that lives inside your browser. Click a hint button on any leetcode.com problem and get a Socratic hint without leaving the page. Track per-pattern mastery (Elo), get spaced-repetition reminders (SM-2), and ask the recommender for a single next problem aimed at any target company you specify.

No API key required — calls go through your existing `claude` CLI subscription via a local Python service.

Status: **v1.1.0** (sessions 1–7 + a usability follow-up round adding markdown rendering, a 4th hint level, and skill/improve modes).

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

- **Find a problem (3 modes)**:
  - **Company** — type any company (autocomplete from your ingested set). Auto-ingests on demand for unknown targets (~5–30 s on first hit). Cold-start expansion engages for thin targets like SpaceX so the pool draws from similar companies (Tesla, NVIDIA, Apple, Anduril, Palantir).
  - **Skill** — pick a pattern from the dropdown (sorted by your Elo, weakest first). Recommender returns a problem of difficulty appropriate to your Elo on that pattern.
  - **Improve** — no input. Auto-targets your weakest attempted pattern at the right difficulty. With no attempts logged yet, falls back to a sensible warmup pattern.
- **Weakest patterns** — your bottom-N pattern Elos. Updates after every finished attempt.
- **Due for review** — SM-2 spaced repetition. Click-through opens leetcode.com.
- **Hint buttons** — 4 levels with explicit prompt contracts:
  - **L1 Pattern** — broad category only (e.g. "this is hashing"). No data structure, no algorithm.
  - **L2 Data structure** — names the structure (e.g. "use a hash map"). No algorithm name.
  - **L3 Strategy** — algorithmic approach in prose. Describes technique by behavior, no formal name, no numbered steps, no code.
  - **L4 Decompose** — numbered subproblems. Algorithm names allowed; still no code.
- **Review my code** — auto-fetches your Monaco-editor code, returns a staff-engineer-style review (bugs first, then complexity in big-O, then style; never writes the better solution for you).
- **Mock interview** — single-response interview round on the current problem: poses it, lists the clarifying questions you should ask with answers, asks for approach before code, pre-empts the wrong direction, demands complexity commitment, throws a follow-up.
- **Attempt logging** — Start → live timer → I'm done → outcome picker (solved / partial / stuck). Code is auto-captured from Monaco. Every hint you take attaches to the active attempt.

Hints render with markdown (bold, inline code, code blocks). Hints use Haiku for ~3–5× faster responses than Sonnet; review and mock keep the default model.

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
# 95 passed
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
