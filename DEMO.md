# DEMO

A reproducible end-to-end walkthrough. Tracks an actual session against the deployed code at `v1.0.0`. All commands run from the repo root unless noted.

## 0. Install (one-time)

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd ..
```

Verify the install:

```bash
.venv/bin/pytest server/tests/
# expected: 74 passed
```

## 1. Launch

```bash
./start.sh
```

What this does:
- Starts the local FastAPI service on `127.0.0.1:8765` (logs at `~/.lc-coach/server.log`).
- Opens a Chrome window with the extension preloaded into `~/.lc-coach/chrome-profile`.
- Lands you on `https://leetcode.com/problemset/all/`.

First run only: sign in to LeetCode in that Chrome window. The session sticks for subsequent launches.

Smoke check the service is up:
```bash
curl -sS http://127.0.0.1:8765/health
# {"ok":true,"service":"lc-coach","version":"0.1.0"}
```

## 2. Ingest the SpaceX-similar company set

```bash
curl -sS -X POST http://127.0.0.1:8765/ingest \
  -H 'Content-Type: application/json' \
  -d '{"companies":["spacex","tesla","apple","nvidia","palantir-technologies","anduril","blue-origin","boeing","microsoft","amazon","google","facebook"]}'
```

Expected (~28 s on first hit, <2 s on subsequent runs from disk cache):
```json
{"companies":11,"problems":6208,"requested":["spacex","tesla","apple","nvidia","palantir-technologies","anduril","blue-origin","boeing","microsoft","amazon","google","facebook"]}
```

Sanity check companies:
```bash
curl -sS http://127.0.0.1:8765/companies | python3 -m json.tool | head -30
```

## 3. Pin the lc-coach toolbar icon and open a problem

In the launched Chrome window:
1. Click the puzzle icon → pin lc-coach.
2. Open `https://leetcode.com/problems/two-sum/`.
3. Click the lc-coach icon. The side panel opens. Status dot top-left should be green.

The side panel auto-detects the problem: shows "Two Sum", "Easy" pill, and tags ("Array", "Hash Table") underneath.

## 4. Drill the problem

In the side panel:
1. Click **Start attempt**. Timer starts.
2. Click **L1 — Pattern**. ~7 s later you'll see something like:

   > "This is a classic lookup / search problem — for each element, you're really asking 'does the thing I need already exist somewhere I've seen?' ..."

3. Type your code in the LeetCode editor. (For the demo, write the brute-force version.)
4. If you get stuck, click **L2 — Data structure**. You'll get the data structure (hash map) without the algorithm:

   > "Reach for a hash map (dictionary) where the keys are the numbers you've already seen ..."

5. Submit on LeetCode. Click **I'm done** in the panel → pick **Solved** → **Save attempt**.

## 5. Watch the mastery panel update

Top of the side panel — "Weakest patterns" — now shows your `arrays` and `hashing` Elos shifting (a clean solve on Easy bumps them up by ~17 each). Numbers stabilize as you do more attempts.

Inspect the SQLite trail directly:
```bash
sqlite3 ~/.lc-coach/state.db \
  "SELECT p.name, ROUND(m.elo,1), m.n_attempts FROM mastery m JOIN patterns p ON p.id = m.pattern_id ORDER BY m.elo"
```

## 6. Get a code review

Without leaving the LeetCode page, click **Review my code** in the side panel.

The extension grabs your current Monaco-editor code and pipes it through `claude -p` with the staff-engineer review prompt. ~15 s later you get something like:

> "Correctness is fine for the happy path. The real issue is complexity. This is O(n²) time, O(1) extra space — for Two Sum that's the brute-force tier, what an interviewer expects you to write *first*, then immediately improve. ... Pick the data structure that answers 'have I seen target − nums[i]' in O(1) and you're done. Derive it — don't peek."

Notably: never names the hash map outright; pushes you to derive it. Staff-level tone, big-O up front.

## 7. Mock interview round

Click **Mock interview**. ~16 s later, a single interview opener:

> "Alright, let's get started. ... I'm going to hand you an array and a target. Return the indices of the two elements that sum to the target. Indices, not values — important.
>
> *Before you touch code, what would you ask me?* I'd expect a strong candidate to nail two or three of these — is the array sorted? Can the same value appear more than once? Negatives? Overflow? ...
>
> *Walk me through your approach before you write anything.* ...
>
> A heads-up: the obvious move is two nested loops — that's O(n²). There's a single-pass solution. Find it.
>
> *Commit to complexity before you code.* ...
>
> Follow-up: be ready for *what changes if the input array is sorted, and we want O(1) extra space?*"

Hits the full contract: pose / clarify-with-answers / approach-before-code / wrong-direction / complexity / follow-up. Without leaking the answer.

## 8. Get a target-company recommendation

Type `spacex` in the **Target company** input and press Enter (or click **Next problem**).

Expected (cold-start expansion engages because SpaceX has only 15 ingested problems, well under the threshold of 30):

```
[next problem]
LRU Cache (Medium)
from spacex's tagged set · ...
```

Status under the input: `target pool: 15 (cold-start expansion engaged)`.
Below the result: `cold-start expansion: pool drew from tesla (0.38), nvidia (0.32), apple (0.31)`.

Try `apple` next:
```
LRU Cache (Medium)
from apple's tagged set
```
No cold-start (Apple has 382 problems).

Try a company you haven't ingested — `stripe`, `databricks`, anything:
```
[5–30 s wait — auto-ingest from snehasishroy + liquidslr]
[next problem]
...
```

## 9. Spaced-repetition wakes you up tomorrow

When you finish an attempt, SM-2 schedules the next review. Stuck or low quality → 1 day. Solved cleanly → escalating intervals (1d → 6d → 6d×ease ≈ 13d → ...).

```bash
curl -sS 'http://127.0.0.1:8765/due?limit=10' | python3 -m json.tool
```

When something becomes due, it appears in the side panel's **Due for review** card with a click-through to the LeetCode URL.

## 10. End the session

`Ctrl-C` in the terminal that ran `start.sh`. The Chrome window with the dedicated profile is yours to keep open or close — closing it doesn't tear down the service unless you Ctrl-C the launcher.

## Reset the database

If you want to start over with a clean slate:
```bash
rm -f ~/.lc-coach/state.db
```

Raw company-tag CSVs are cached separately at `~/.lc-coach/companies-raw/` and survive the DB reset (faster re-ingest on the next run).
