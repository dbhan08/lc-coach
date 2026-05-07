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
        finished = r.json()
        assert finished["outcome"] == "solved"
        assert finished["ended_at"] is not None
        assert finished["code_snapshot"] == "def two_sum(...): ..."
        assert finished["language"] == "python"

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
