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
