from lc_coach import db


def test_init_db_idempotent(tmp_db):
    db.init_db()
    db.init_db()  # second call must not raise
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = [r[0] for r in rows]
    assert "problems" in names
    assert "hints" in names


def test_upsert_problem_and_get(tmp_db):
    db.init_db()
    with db.connect() as conn:
        db.upsert_problem(
            conn,
            slug="two-sum",
            title="Two Sum",
            statement="Given an array...",
            difficulty="Easy",
            tags=["array", "hash-map"],
        )
        first = db.get_problem(conn, "two-sum")
    assert first is not None
    assert first["title"] == "Two Sum"
    assert first["tags"] == ["array", "hash-map"]
    first_seen = first["first_seen"]

    # second upsert with new title — first_seen should NOT change
    with db.connect() as conn:
        db.upsert_problem(
            conn,
            slug="two-sum",
            title="Two Sum (updated)",
            statement="Given an array...",
            difficulty="Easy",
            tags=["array"],
        )
        second = db.get_problem(conn, "two-sum")
    assert second["title"] == "Two Sum (updated)"
    assert second["first_seen"] == first_seen
    assert second["tags"] == ["array"]


def test_record_and_get_hints(tmp_db):
    db.init_db()
    with db.connect() as conn:
        db.upsert_problem(
            conn, slug="two-sum", title="Two Sum", statement="...", difficulty="Easy"
        )
        for level in (1, 2, 3):
            db.record_hint(
                conn,
                problem_slug="two-sum",
                level=level,
                prompt=f"prompt L{level}",
                response=f"response L{level}",
            )
        hints = db.get_recent_hints(conn, "two-sum", limit=5)
    assert len(hints) == 3
    # most recent first
    assert hints[0]["level"] == 3
    assert hints[-1]["level"] == 1


def test_attempt_lifecycle(tmp_db):
    import pytest

    db.init_db()
    with db.connect() as conn:
        db.upsert_problem(
            conn, slug="two-sum", title="Two Sum", statement="...", difficulty="Easy"
        )

        assert db.get_active_attempt(conn, "two-sum") is None

        a1 = db.start_attempt(conn, problem_slug="two-sum")
        assert a1["id"] >= 1
        assert a1["ended_at"] is None
        assert a1["problem_slug"] == "two-sum"

        # idempotent: starting again returns the same active attempt
        a2 = db.start_attempt(conn, problem_slug="two-sum")
        assert a2["id"] == a1["id"]

        active = db.get_active_attempt(conn, "two-sum")
        assert active is not None and active["id"] == a1["id"]

        finished = db.finish_attempt(
            conn,
            attempt_id=a1["id"],
            outcome="solved",
            code_snapshot="def two_sum(...): ...",
            language="python",
        )
        assert finished["outcome"] == "solved"
        assert finished["ended_at"] is not None
        assert finished["time_spent_seconds"] is not None
        assert finished["code_snapshot"] == "def two_sum(...): ..."
        assert finished["language"] == "python"

        # no longer active
        assert db.get_active_attempt(conn, "two-sum") is None

        # finishing again raises
        with pytest.raises(ValueError, match="already finished"):
            db.finish_attempt(
                conn,
                attempt_id=a1["id"],
                outcome="solved",
                code_snapshot=None,
            )

        # invalid outcome raises
        a3 = db.start_attempt(conn, problem_slug="two-sum")
        with pytest.raises(ValueError, match="outcome must be"):
            db.finish_attempt(
                conn, attempt_id=a3["id"], outcome="bogus", code_snapshot=None
            )


def test_hint_attaches_to_active_attempt_via_record_hint(tmp_db):
    db.init_db()
    with db.connect() as conn:
        db.upsert_problem(
            conn, slug="two-sum", title="Two Sum", statement="...", difficulty="Easy"
        )
        attempt = db.start_attempt(conn, problem_slug="two-sum")
        hint_id = db.record_hint(
            conn,
            problem_slug="two-sum",
            level=1,
            prompt="p",
            response="r",
            attempt_id=attempt["id"],
        )
        row = conn.execute(
            "SELECT attempt_id FROM hints WHERE id = ?", (hint_id,)
        ).fetchone()
    assert row[0] == attempt["id"]
