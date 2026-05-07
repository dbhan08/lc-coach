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
