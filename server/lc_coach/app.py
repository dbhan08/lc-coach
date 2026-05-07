"""FastAPI app exposing the local lc-coach service."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from lc_coach import db
from lc_coach.coach import CoachError, build_hint_prompt, claude_p
from lc_coach.mastery import map_leetcode_tags_to_patterns


def create_app() -> FastAPI:
    app = FastAPI(title="lc-coach", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(chrome-extension://[a-zA-Z0-9-]+|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?)$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        db.init_db()

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "service": "lc-coach", "version": "0.1.0"}

    @app.post("/problems", response_model=ProblemOut)
    def upsert_problem(body: ProblemIn) -> ProblemOut:
        with db.connect() as conn:
            db.upsert_problem(
                conn,
                slug=body.slug,
                title=body.title,
                statement=body.statement,
                difficulty=body.difficulty,
                tags=body.tags,
            )
            patterns = map_leetcode_tags_to_patterns(body.tags or [])
            attached = db.assign_patterns_to_problem(
                conn, slug=body.slug, pattern_names=patterns
            )
            stored = db.get_problem(conn, body.slug)
        assert stored is not None
        return ProblemOut(**{
            "slug": stored["slug"],
            "title": stored["title"],
            "difficulty": stored.get("difficulty"),
            "tags": stored.get("tags", []),
            "patterns": attached,
            "first_seen": stored["first_seen"],
        })

    @app.post("/hint", response_model=HintOut)
    def hint(body: HintIn) -> HintOut:
        if body.level not in (1, 2, 3):
            raise HTTPException(status_code=400, detail="level must be 1, 2, or 3")
        with db.connect() as conn:
            problem = db.get_problem(conn, body.slug)
            if problem is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"problem '{body.slug}' not registered; POST /problems first",
                )
            prior = db.get_recent_hints(conn, body.slug, limit=5)
            active = db.get_active_attempt(conn, body.slug)

        prompt = build_hint_prompt(
            title=problem["title"],
            statement=problem["statement"],
            difficulty=problem.get("difficulty"),
            level=body.level,
            prior_hints=prior,
        )
        try:
            response_text = claude_p(prompt, model=body.model)
        except CoachError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        with db.connect() as conn:
            hint_id = db.record_hint(
                conn,
                problem_slug=body.slug,
                level=body.level,
                prompt=prompt,
                response=response_text,
                attempt_id=active["id"] if active else None,
            )

        return HintOut(
            hint_id=hint_id,
            level=body.level,
            response=response_text,
            attempt_id=active["id"] if active else None,
        )

    @app.post("/attempts/start", response_model=AttemptOut)
    def attempt_start(body: AttemptStartIn) -> AttemptOut:
        with db.connect() as conn:
            problem = db.get_problem(conn, body.slug)
            if problem is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"problem '{body.slug}' not registered; POST /problems first",
                )
            attempt = db.start_attempt(conn, problem_slug=body.slug)
        return AttemptOut(**attempt)

    @app.post("/attempts/done", response_model=AttemptDoneOut)
    def attempt_done(body: AttemptDoneIn) -> AttemptDoneOut:
        try:
            with db.connect() as conn:
                finished = db.finish_attempt(
                    conn,
                    attempt_id=body.attempt_id,
                    outcome=body.outcome,
                    code_snapshot=body.code_snapshot,
                    language=body.language,
                )
                mastery_updates = db.update_mastery_for_attempt(
                    conn, body.attempt_id
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return AttemptDoneOut(
            attempt=AttemptOut(**finished),
            mastery_updates=[MasteryUpdate(**u) for u in mastery_updates],
        )

    @app.get("/attempts/active", response_model=Optional[AttemptOut])
    def attempt_active(slug: str) -> Optional[AttemptOut]:
        with db.connect() as conn:
            row = db.get_active_attempt(conn, slug)
        return AttemptOut(**row) if row else None

    @app.get("/weak", response_model=list[PatternMastery])
    def weak(n: int = 5) -> list[PatternMastery]:
        with db.connect() as conn:
            rows = db.get_weakest_patterns(conn, n=n)
        return [PatternMastery(**r) for r in rows]

    @app.get("/mastery", response_model=list[PatternMastery])
    def mastery_full() -> list[PatternMastery]:
        with db.connect() as conn:
            rows = db.get_full_mastery(conn)
        return [PatternMastery(**r) for r in rows]

    return app


# --- Pydantic models -------------------------------------------------------


class ProblemIn(BaseModel):
    slug: str = Field(..., min_length=1)
    title: str
    statement: str
    difficulty: Optional[str] = None
    tags: Optional[list[str]] = None


class ProblemOut(BaseModel):
    slug: str
    title: str
    difficulty: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    first_seen: str


class HintIn(BaseModel):
    slug: str
    level: int = Field(..., ge=1, le=3)
    model: Optional[str] = None


class HintOut(BaseModel):
    hint_id: int
    level: int
    response: str
    attempt_id: Optional[int] = None


class AttemptStartIn(BaseModel):
    slug: str = Field(..., min_length=1)


class AttemptDoneIn(BaseModel):
    attempt_id: int
    outcome: Literal["solved", "partial", "stuck"]
    code_snapshot: Optional[str] = None
    language: Optional[str] = None


class AttemptOut(BaseModel):
    id: int
    problem_slug: str
    started_at: str
    ended_at: Optional[str] = None
    outcome: Optional[str] = None
    code_snapshot: Optional[str] = None
    language: Optional[str] = None
    time_spent_seconds: Optional[int] = None


class MasteryUpdate(BaseModel):
    pattern_id: int
    pattern_name: str
    old_elo: float
    new_elo: float
    delta: float
    n_attempts: int
    score: float


class AttemptDoneOut(BaseModel):
    attempt: AttemptOut
    mastery_updates: list[MasteryUpdate] = Field(default_factory=list)


class PatternMastery(BaseModel):
    id: int
    name: str
    elo: Optional[float] = None
    n_attempts: int = 0
    last_updated: Optional[str] = None


# Module-level instance for `uvicorn lc_coach.app:app`
app = create_app()
