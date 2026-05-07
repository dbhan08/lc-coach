# lc-coach

A personal LeetCode coach that lives in your browser. Click a hint button on any leetcode.com problem and get a Socratic hint without leaving the page.

Status: **v0.6 (Session 2)** — hint flow + full attempt logging with auto-captured Monaco code. Pattern mastery, spaced repetition, and the company-similarity recommender land in sessions 3–7.

## Run it

One-time install:
```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Then every day:
```bash
./start.sh
```

That boots the service on `127.0.0.1:8765` and opens a Chrome window with the extension preloaded into a dedicated profile (`~/.lc-coach/chrome-profile`). On the first run you'll sign in to LeetCode in that window — the session sticks for subsequent launches. Ctrl-C in the launching terminal stops the service.

You can also pass a starting URL: `./start.sh https://leetcode.com/problems/two-sum/`.

If you'd rather use your main Chrome profile, skip the launcher: start `python -m lc_coach` yourself, then `chrome://extensions` → Developer mode → Load unpacked → select `extension/`.

## Usage

On a LeetCode problem page, the side panel auto-detects the problem and shows three buttons:

- **L1 — Pattern**: gives the broad pattern category only (e.g. "this is a hashing problem"). No data structure, no algorithm name.
- **L2 — Data structure**: names the data structure(s) you'll reach for. No algorithm name.
- **L3 — Decompose**: spells out the subproblems and the algorithmic insight, stopping just short of code.

Hints stream in from your local Claude Code subscription via `claude -p` — no API keys, no separate billing. Each hint takes ~3–10 seconds.

The status dot top-left indicates local-service health (green = reachable, red = service down). Run `python -m lc_coach` again if it goes red.

## How it works

- **Browser extension (MV3, Chrome)** — content script reads the problem from the LeetCode DOM (title from `document.title`, statement from the description container, tags from `/tag/<slug>` anchors). Side panel posts `/problems` and `/hint` to the local service.
- **Local Python service (FastAPI on `127.0.0.1:8765`)** — owns SQLite state at `~/.lc-coach/state.db`, builds versioned hint prompts with explicit per-level contracts, shells out to your installed `claude` CLI, logs every hint.
- **No API key** — `lc_coach.coach.claude_p()` is a thin subprocess wrapper around the same `claude` binary you already use for Claude Code. The cost is your existing subscription.

## Verify Session 1 works

1. Service: `curl http://127.0.0.1:8765/health` → `{"ok": true, ...}`
2. Tests: `cd server && .venv/bin/pytest` → 13 passed.
3. End-to-end: load extension, open any LeetCode problem, click L1 → hint about the pattern category appears in the side panel within ~10s.
4. SQLite trail: `sqlite3 ~/.lc-coach/state.db "SELECT level, substr(response,1,80) FROM hints ORDER BY id DESC LIMIT 3"` shows your recent hints.

## Stack

Python 3.9+, FastAPI, uvicorn, SQLite (stdlib), `claude` CLI (subprocess), Chrome MV3 (vanilla JS), pytest.

## Roadmap

- ~~Session 1 — Foundation: extension + service + working hint flow~~ ✓
- ~~Session 2 — Monaco editor code reading + full attempt logging~~ ✓
- Session 3 — Per-pattern Elo mastery model
- Session 4 — Public company-tag ingest + SM-2 spaced repetition
- Session 5 — Company similarity recommender + target-company workflow
- Session 6 — Code review + mock-interview modes; prompt-quality golden tests
- Session 7 — Docs, demo, public ship
