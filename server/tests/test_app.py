from fastapi.testclient import TestClient

from lc_coach import coach
from lc_coach.app import create_app


def _client(tmp_db):
    return TestClient(create_app())


def test_health(tmp_db):
    with _client(tmp_db) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_problem_upsert_and_hint_flow(tmp_db, monkeypatch):
    monkeypatch.setattr(coach, "claude_p", lambda prompt, **kw: "this is a hashing problem")
    # patch where it's used too
    from lc_coach import app as app_mod
    monkeypatch.setattr(app_mod, "claude_p", lambda prompt, **kw: "this is a hashing problem")

    with _client(tmp_db) as c:
        r = c.post(
            "/problems",
            json={
                "slug": "two-sum",
                "title": "Two Sum",
                "statement": "Given an integer array nums and a target...",
                "difficulty": "Easy",
                "tags": ["array", "hash-map"],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["slug"] == "two-sum"
        assert body["tags"] == ["array", "hash-map"]

        r = c.post("/hint", json={"slug": "two-sum", "level": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["level"] == 1
        assert "hashing" in body["response"]
        assert body["hint_id"] >= 1


def test_hint_unknown_problem_404(tmp_db):
    with _client(tmp_db) as c:
        r = c.post("/hint", json={"slug": "nope", "level": 1})
    assert r.status_code == 404


def test_hint_invalid_level_422(tmp_db):
    with _client(tmp_db) as c:
        r = c.post("/hint", json={"slug": "two-sum", "level": 7})
    # pydantic returns 422 for ge/le validation
    assert r.status_code == 422


def test_hint_level_4_accepted_as_decompose(tmp_db, monkeypatch):
    from lc_coach import app as app_mod

    monkeypatch.setattr(app_mod, "claude_p", lambda p, **kw: "step 1...")
    with _client(tmp_db) as c:
        _register_problem(c)
        r = c.post("/hint", json={"slug": "two-sum", "level": 4})
    assert r.status_code == 200
    assert r.json()["level"] == 4


def _register_problem(client):
    return client.post(
        "/problems",
        json={
            "slug": "two-sum",
            "title": "Two Sum",
            "statement": "Given an integer array nums and a target...",
            "difficulty": "Easy",
            "tags": ["array", "hash-map"],
        },
    )


def test_attempt_lifecycle_via_api(tmp_db):
    with _client(tmp_db) as c:
        _register_problem(c)

        assert c.get("/attempts/active", params={"slug": "two-sum"}).json() is None

        r = c.post("/attempts/start", json={"slug": "two-sum"})
        assert r.status_code == 200, r.text
        a1 = r.json()
        assert a1["ended_at"] is None
        assert a1["problem_slug"] == "two-sum"

        # idempotent
        r2 = c.post("/attempts/start", json={"slug": "two-sum"})
        assert r2.json()["id"] == a1["id"]

        active = c.get("/attempts/active", params={"slug": "two-sum"}).json()
        assert active and active["id"] == a1["id"]

        r = c.post(
            "/attempts/done",
            json={
                "attempt_id": a1["id"],
                "outcome": "solved",
                "code_snapshot": "def two_sum(...): ...",
                "language": "python",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        finished = body["attempt"]
        assert finished["outcome"] == "solved"
        assert finished["ended_at"] is not None
        assert finished["code_snapshot"] == "def two_sum(...): ..."
        assert finished["language"] == "python"
        assert isinstance(body["mastery_updates"], list)

        # active is gone
        assert c.get("/attempts/active", params={"slug": "two-sum"}).json() is None


def test_hint_tags_active_attempt(tmp_db, monkeypatch):
    from lc_coach import app as app_mod

    monkeypatch.setattr(app_mod, "claude_p", lambda prompt, **kw: "category-only hint")

    with _client(tmp_db) as c:
        _register_problem(c)
        a = c.post("/attempts/start", json={"slug": "two-sum"}).json()

        r = c.post("/hint", json={"slug": "two-sum", "level": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["attempt_id"] == a["id"]


def test_hint_no_attempt_id_when_no_active(tmp_db, monkeypatch):
    from lc_coach import app as app_mod

    monkeypatch.setattr(app_mod, "claude_p", lambda prompt, **kw: "category-only hint")

    with _client(tmp_db) as c:
        _register_problem(c)

        r = c.post("/hint", json={"slug": "two-sum", "level": 1})
        assert r.status_code == 200
        assert r.json()["attempt_id"] is None


def test_attempt_done_invalid_outcome_422(tmp_db):
    with _client(tmp_db) as c:
        _register_problem(c)
        a = c.post("/attempts/start", json={"slug": "two-sum"}).json()
        r = c.post(
            "/attempts/done",
            json={"attempt_id": a["id"], "outcome": "winner"},
        )
    # pydantic Literal rejects with 422
    assert r.status_code == 422


def test_attempt_done_unknown_id_400(tmp_db):
    with _client(tmp_db) as c:
        _register_problem(c)
        r = c.post(
            "/attempts/done",
            json={"attempt_id": 9999, "outcome": "solved"},
        )
    assert r.status_code == 400


def test_attempt_start_unknown_problem_404(tmp_db):
    with _client(tmp_db) as c:
        r = c.post("/attempts/start", json={"slug": "nope"})
    assert r.status_code == 404


def test_review_round_trip(tmp_db, monkeypatch):
    from lc_coach import app as app_mod

    captured = {}

    def fake_claude_p(prompt, **kw):
        captured["prompt"] = prompt
        captured["kw"] = kw
        return "review: looks fine; complexity is O(n)."

    monkeypatch.setattr(app_mod, "claude_p", fake_claude_p)

    with _client(tmp_db) as c:
        _register_problem(c)
        r = c.post(
            "/review",
            json={
                "slug": "two-sum",
                "code": "def two_sum(nums, target): return []",
                "language": "python",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "two-sum"
    assert "looks fine" in body["response"]
    # Code round-trip into the prompt that gets shipped to claude
    assert "def two_sum(nums, target): return []" in captured["prompt"]


def test_review_unknown_problem_404(tmp_db):
    with _client(tmp_db) as c:
        r = c.post("/review", json={"slug": "ghost", "code": "x = 1"})
    assert r.status_code == 404


def test_mock_round_trip(tmp_db, monkeypatch):
    from lc_coach import app as app_mod

    monkeypatch.setattr(
        app_mod,
        "claude_p",
        lambda p, **kw: "Round 1. Pose the problem... Clarifying questions...",
    )

    with _client(tmp_db) as c:
        _register_problem(c)
        r = c.post("/mock", json={"slug": "two-sum"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "two-sum"
    assert "Pose the problem" in body["response"]


def test_mock_unknown_problem_404(tmp_db):
    with _client(tmp_db) as c:
        r = c.post("/mock", json={"slug": "ghost"})
    assert r.status_code == 404


def test_complexity_round_trip(tmp_db, monkeypatch):
    from lc_coach import app as app_mod

    captured = {}

    def fake_claude_p(prompt, **kw):
        captured["prompt"] = prompt
        return "Time: O(n²) — nested loops over the array.\nSpace: O(1) — no extra structures."

    monkeypatch.setattr(app_mod, "claude_p", fake_claude_p)

    with _client(tmp_db) as c:
        _register_problem(c)
        r = c.post(
            "/complexity",
            json={
                "slug": "two-sum",
                "code": "for i in range(len(nums)):\n    for j in range(i+1, len(nums)): ...",
                "language": "python",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "two-sum"
    assert "Time:" in body["response"]
    assert "Space:" in body["response"]
    # Code round-tripped into the prompt that was sent to claude
    assert "for j in range(i+1" in captured["prompt"]


def test_complexity_unknown_problem_404(tmp_db):
    with _client(tmp_db) as c:
        r = c.post("/complexity", json={"slug": "ghost", "code": "x = 1"})
    assert r.status_code == 404


def _ingest_some_data(c):
    """Plant a tiny set of company_problems + problem stubs for skill-mode tests."""
    # Manually call the DB layer through the API would require a real /ingest;
    # easier: drive the underlying helpers directly via a tiny POST-friendly
    # path by re-using /problems for each.
    for slug, title, diff, tags in [
        ("two-sum", "Two Sum", "Easy", ["Array", "Hash Table"]),
        ("group-anagrams", "Group Anagrams", "Medium", ["Hash Table", "String"]),
        ("valid-anagram", "Valid Anagram", "Easy", ["Hash Table"]),
        ("course-schedule", "Course Schedule", "Medium", ["Topological Sort", "Graph"]),
        ("alien-dictionary", "Alien Dictionary", "Hard", ["Topological Sort"]),
    ]:
        c.post(
            "/problems",
            json={
                "slug": slug,
                "title": title,
                "statement": "...",
                "difficulty": diff,
                "tags": tags,
            },
        )


def test_skill_mode_returns_problem(tmp_db):
    with _client(tmp_db) as c:
        _ingest_some_data(c)
        r = c.get("/next", params={"mode": "skill", "pattern": "hashing"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "skill"
    assert body["pattern"] == "hashing"
    # All three hashing-tagged problems are candidates; pool size reflects that
    assert body["target_pool_size"] >= 3
    assert body["slug"] in {"two-sum", "group-anagrams", "valid-anagram"}


def test_skill_mode_requires_pattern(tmp_db):
    with _client(tmp_db) as c:
        r = c.get("/next", params={"mode": "skill"})
    assert r.status_code == 400


def test_skill_mode_unknown_pattern_404(tmp_db):
    with _client(tmp_db) as c:
        r = c.get("/next", params={"mode": "skill", "pattern": "made-up-pattern"})
    assert r.status_code == 404


def test_unknown_mode_400(tmp_db):
    with _client(tmp_db) as c:
        r = c.get("/next", params={"mode": "garbage", "target": "apple"})
    assert r.status_code == 400


def test_company_mode_requires_target_when_no_target(tmp_db):
    with _client(tmp_db) as c:
        r = c.get("/next", params={"mode": "company"})
    assert r.status_code == 400


def test_improve_mode_falls_back_with_no_attempts(tmp_db):
    with _client(tmp_db) as c:
        _ingest_some_data(c)
        r = c.get("/next", params={"mode": "improve"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "improve"
    # No attempts → fallback to 'hashing' warmup
    assert body["pattern"] == "hashing"
    assert "no attempts yet" in body["rationale"]


def test_premium_mark_unmark_roundtrip(tmp_db):
    with _client(tmp_db) as c:
        _register_problem(c)
        # Initially empty
        assert c.get("/premium").json() == []

        r = c.post("/problems/two-sum/premium")
        assert r.status_code == 200
        assert r.json() == {"slug": "two-sum", "marked": True}

        listed = c.get("/premium").json()
        assert len(listed) == 1 and listed[0]["slug"] == "two-sum"

        # Idempotent re-mark
        c.post("/problems/two-sum/premium")
        assert len(c.get("/premium").json()) == 1

        r = c.delete("/problems/two-sum/premium")
        assert r.json() == {"slug": "two-sum", "marked": False}
        assert c.get("/premium").json() == []


def test_company_mode_skips_premium_slugs(tmp_db):
    with _client(tmp_db) as c:
        _ingest_some_data(c)
        # Mark valid-anagram premium so the recommender should never pick it
        c.post("/problems/valid-anagram/premium")
        # Try /next a few times for hashing skill; valid-anagram should never appear
        for _ in range(8):
            r = c.get("/next", params={"mode": "skill", "pattern": "hashing"})
            assert r.status_code == 200, r.text
            assert r.json()["slug"] != "valid-anagram"


def test_skill_mode_excludes_one_shot_slugs(tmp_db):
    """`exclude=slug1,slug2` should hide those slugs for this call only —
    no persistent flag, just a one-shot 'give me something else'."""
    with _client(tmp_db) as c:
        _ingest_some_data(c)
        # Without exclude, hashing has 3 candidates (two-sum, group-anagrams, valid-anagram)
        first = c.get("/next", params={"mode": "skill", "pattern": "hashing"}).json()
        first_slug = first["slug"]
        # Ask again, excluding the one we just got
        second = c.get(
            "/next",
            params={"mode": "skill", "pattern": "hashing", "exclude": first_slug},
        ).json()
        assert second["slug"] != first_slug
        # And the original is NOT permanent — calling without exclude returns it again
        third = c.get("/next", params={"mode": "skill", "pattern": "hashing"}).json()
        # third may or may not equal first depending on tiebreaks; the key is
        # nothing got permanent-flagged
        assert c.get("/premium").json() == []


def test_exclude_beats_due_for_review(tmp_db):
    """An excluded slug must stay hidden even when it is due for review.

    Regression: exclusions used to be merged into `recent_slugs`, and the
    recency filter spares anything that is due. Two due problems then
    ping-ponged forever behind "Show different one".
    """
    from lc_coach import db

    with _client(tmp_db) as c:
        _ingest_some_data(c)
        # Make every hashing problem due today.
        with db.connect() as conn:
            for slug in ("two-sum", "group-anagrams", "valid-anagram"):
                conn.execute(
                    """
                    INSERT INTO reviews
                      (problem_slug, ease, repetitions, interval_days,
                       due_date, last_quality, last_reviewed_at)
                    VALUES (?, 2.5, 1, 1, '2000-01-01', 3, '2000-01-01')
                    ON CONFLICT(problem_slug) DO NOTHING
                    """,
                    (slug,),
                )

        seen = []
        excluded = []
        for _ in range(3):
            params = {"mode": "skill", "pattern": "hashing"}
            if excluded:
                params["exclude"] = ",".join(excluded)
            r = c.get("/next", params=params)
            assert r.status_code == 200, r.text
            slug = r.json()["slug"]
            assert slug not in excluded
            seen.append(slug)
            excluded.append(slug)

        assert len(set(seen)) == 3

        # Every candidate is now excluded. The service must say so, not
        # recycle a due problem the user already skipped.
        r = c.get(
            "/next",
            params={
                "mode": "skill",
                "pattern": "hashing",
                "exclude": ",".join(excluded),
            },
        )
        assert r.status_code == 404, r.text


def test_company_mode_returns_404_when_all_premium(tmp_db):
    with _client(tmp_db) as c:
        _ingest_some_data(c)
        # Mark every hashing-tagged problem premium
        for slug in ("two-sum", "group-anagrams", "valid-anagram"):
            c.post(f"/problems/{slug}/premium")
        r = c.get("/next", params={"mode": "skill", "pattern": "hashing"})
    assert r.status_code == 404
    assert "premium" in r.json()["detail"].lower()


def test_improve_mode_targets_weakest_with_attempts(tmp_db, monkeypatch):
    from lc_coach import app as app_mod

    monkeypatch.setattr(app_mod, "claude_p", lambda p, **kw: "x")

    with _client(tmp_db) as c:
        _ingest_some_data(c)
        # Bomb topological-sort by getting stuck on Alien Dictionary (tagged
        # ONLY with topological-sort, not graph — so the drop is unambiguous).
        a = c.post("/attempts/start", json={"slug": "alien-dictionary"}).json()
        c.post(
            "/attempts/done",
            json={"attempt_id": a["id"], "outcome": "stuck"},
        )

        # Solve a hashing problem cleanly to push hashing UP (so it isn't the weakest).
        a2 = c.post("/attempts/start", json={"slug": "two-sum"}).json()
        c.post(
            "/attempts/done",
            json={"attempt_id": a2["id"], "outcome": "solved"},
        )

        r = c.get("/next", params={"mode": "improve"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "improve"
    # Topological-sort is the only attempted pattern that dropped, so improve
    # mode targets it.
    assert body["pattern"] == "topological-sort"
    assert body["slug"] in {"course-schedule", "alien-dictionary"}
