"""FastAPI app exposing the local lc-coach service."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from lc_coach import db
from lc_coach.coach import (
    CoachError,
    build_hint_prompt,
    build_mock_prompt,
    build_review_prompt,
    claude_p,
)
from lc_coach.ingest import aggregate, default_sources, normalize_company
from lc_coach.mastery import map_leetcode_tags_to_patterns
from lc_coach.recommend import (
    COLD_START_THRESHOLD,
    expand_pool,
    needs_cold_start,
    pick_next,
    pick_skill_next,
    rank_similar,
)


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
        if body.level not in (1, 2, 3, 4):
            raise HTTPException(status_code=400, detail="level must be 1, 2, 3, or 4")
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
                review = db.update_review_for_attempt(conn, body.attempt_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return AttemptDoneOut(
            attempt=AttemptOut(**finished),
            mastery_updates=[MasteryUpdate(**u) for u in mastery_updates],
            review=ReviewOut(**review) if review else None,
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

    @app.post("/ingest", response_model=IngestOut)
    def ingest(body: IngestIn) -> IngestOut:
        sources = default_sources()
        try:
            aggregated = aggregate(
                sources, companies_filter=body.companies
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"ingest failed: {exc}")
        with db.connect() as conn:
            counts = db.store_aggregated_companies(conn, aggregated)
        return IngestOut(
            companies=counts["companies"],
            problems=counts["problems"],
            requested=list(body.companies) if body.companies else None,
        )

    @app.get("/companies", response_model=list[CompanyRow])
    def list_companies() -> list[CompanyRow]:
        with db.connect() as conn:
            rows = db.get_companies_with_counts(conn)
        return [CompanyRow(**r) for r in rows]

    @app.get("/companies/{name}", response_model=list[CompanyProblemRow])
    def company_problems(name: str, limit: int = 25) -> list[CompanyProblemRow]:
        with db.connect() as conn:
            rows = db.get_company_problems(conn, name, limit=limit)
        return [CompanyProblemRow(**r) for r in rows]

    @app.get("/due", response_model=list[DueRow])
    def due(limit: int = 20) -> list[DueRow]:
        with db.connect() as conn:
            rows = db.get_due_problems(conn, limit=limit)
        return [DueRow(**r) for r in rows]

    @app.post("/review", response_model=ReviewCodeOut)
    def review_code(body: ReviewCodeIn) -> ReviewCodeOut:
        with db.connect() as conn:
            problem = db.get_problem(conn, body.slug)
        if problem is None:
            raise HTTPException(
                status_code=404,
                detail=f"problem '{body.slug}' not registered; POST /problems first",
            )
        prompt = build_review_prompt(
            title=problem["title"],
            statement=problem["statement"],
            difficulty=problem.get("difficulty"),
            code=body.code,
            language=body.language,
        )
        try:
            response_text = claude_p(prompt, model=body.model)
        except CoachError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return ReviewCodeOut(slug=body.slug, response=response_text)

    @app.post("/mock", response_model=MockOut)
    def mock_interview(body: MockIn) -> MockOut:
        with db.connect() as conn:
            problem = db.get_problem(conn, body.slug)
        if problem is None:
            raise HTTPException(
                status_code=404,
                detail=f"problem '{body.slug}' not registered; POST /problems first",
            )
        prompt = build_mock_prompt(
            title=problem["title"],
            statement=problem["statement"],
            difficulty=problem.get("difficulty"),
        )
        try:
            response_text = claude_p(prompt, model=body.model)
        except CoachError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return MockOut(slug=body.slug, response=response_text)

    @app.get("/similar/{name}", response_model=list[SimilarCompany])
    def similar(name: str, k: int = 5) -> list[SimilarCompany]:
        canonical = normalize_company(name)
        with db.connect() as conn:
            profiles = db.load_all_company_profiles(conn)
        target = profiles.get(canonical)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"company '{canonical}' not in DB; ingest it first via POST /ingest",
            )
        ranked = rank_similar(target, profiles.values(), k=k)
        return [
            SimilarCompany(
                name=other.name,
                score=score,
                n_problems=other.n_problems,
            )
            for other, score in ranked
        ]

    @app.get("/next", response_model=NextOut)
    def next_problem(
        target: Optional[str] = None,
        mode: str = "company",
        pattern: Optional[str] = None,
        auto_ingest: bool = True,
        k_similar: int = 5,
    ) -> NextOut:
        mode = (mode or "company").strip().lower()
        if mode == "skill":
            if not pattern:
                raise HTTPException(
                    status_code=400, detail="skill mode requires `pattern`"
                )
            return _next_skill(pattern.strip().lower())
        if mode == "improve":
            return _next_improve()
        if mode != "company":
            raise HTTPException(status_code=400, detail=f"unknown mode: {mode!r}")

        if not target:
            raise HTTPException(status_code=400, detail="company mode requires `target`")
        canonical = normalize_company(target)
        if not canonical:
            raise HTTPException(status_code=400, detail="empty target")

        with db.connect() as conn:
            profiles = db.load_all_company_profiles(conn)

        if canonical not in profiles:
            if not auto_ingest:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"company '{canonical}' not in DB. Re-call with "
                        f"auto_ingest=true or POST /ingest first."
                    ),
                )
            try:
                aggregated = aggregate(
                    default_sources(), companies_filter=[canonical]
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=502, detail=f"ingest failed: {exc}"
                )
            with db.connect() as conn:
                counts = db.store_aggregated_companies(conn, aggregated)
                profiles = db.load_all_company_profiles(conn)
            if canonical not in profiles or counts["problems"] == 0:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"company '{canonical}' produced no rows from any source. "
                        "Try a different target."
                    ),
                )

        target_profile = profiles[canonical]
        ranked_similar = rank_similar(
            target_profile, profiles.values(), k=k_similar
        )

        with db.connect() as conn:
            target_entries = db.get_pool_entries(conn, canonical)
            similar_entries = {
                other.name: db.get_pool_entries(conn, other.name)
                for other, _ in ranked_similar
            }
            cold_start = needs_cold_start(target_profile)
            pool = expand_pool(
                target_profile,
                ranked_similar if cold_start else [],
                target_entries=target_entries,
                similar_entries_by_company=similar_entries if cold_start else {},
            )
            slugs = [e.slug for e in pool]
            patterns_by_slug = db.get_problem_pattern_map(conn, slugs)
            due_rows = db.get_due_problems(conn, limit=1000)
            due_slugs = {r["problem_slug"] for r in due_rows}
            recent_slugs = db.get_recent_attempt_slugs(conn, days=7)
            weak_patterns = db.get_weakest_pattern_names(conn, n=3)

        chosen = pick_next(
            pool,
            weak_patterns_by_slug=patterns_by_slug,
            due_slugs=due_slugs,
            recent_slugs=recent_slugs,
            user_weak_patterns=weak_patterns,
            target_name=canonical,
        )
        if chosen is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no candidates for '{canonical}'. Both target and similar "
                    "companies are empty — try a different target."
                ),
            )

        return NextOut(
            slug=chosen.slug,
            title=chosen.title,
            difficulty=chosen.difficulty,
            leetcode_url=f"https://leetcode.com/problems/{chosen.slug}/",
            score=chosen.score,
            from_company=chosen.company,
            rationale=" · ".join(chosen.rationale_parts),
            cold_start_used=cold_start,
            target=canonical,
            target_pool_size=target_profile.n_problems,
            similar_companies=[
                SimilarCompany(name=p.name, score=s, n_problems=p.n_problems)
                for p, s in ranked_similar
            ],
            user_weak_patterns=weak_patterns,
            mode="company",
        )

    def _next_skill(pattern_name: str) -> NextOut:
        from lc_coach.mastery import COARSE_PATTERNS

        if pattern_name not in COARSE_PATTERNS:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"unknown pattern '{pattern_name}'. "
                    f"Valid: {', '.join(COARSE_PATTERNS)}"
                ),
            )
        with db.connect() as conn:
            candidates = db.get_problems_by_pattern(conn, pattern_name, limit=200)
            pattern_elo = db.get_pattern_elo(conn, pattern_name)
            due_rows = db.get_due_problems(conn, limit=1000)
            due_slugs = {r["problem_slug"] for r in due_rows}
            recent_slugs = db.get_recent_attempt_slugs(conn, days=7)

        if not candidates:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no problems tagged with '{pattern_name}' in the DB. "
                    "Run /ingest to populate."
                ),
            )

        chosen = pick_skill_next(
            candidates,
            pattern_elo=pattern_elo,
            due_slugs=due_slugs,
            recent_slugs=recent_slugs,
            pattern_name=pattern_name,
        )
        if chosen is None:
            raise HTTPException(status_code=404, detail="no candidates")

        return NextOut(
            slug=chosen.slug,
            title=chosen.title,
            difficulty=chosen.difficulty,
            leetcode_url=f"https://leetcode.com/problems/{chosen.slug}/",
            score=chosen.score,
            from_company="(skill mode)",
            rationale=" · ".join(chosen.rationale_parts),
            cold_start_used=False,
            target=pattern_name,
            target_pool_size=len(candidates),
            similar_companies=[],
            user_weak_patterns=[],
            mode="skill",
            pattern=pattern_name,
            pattern_elo=pattern_elo,
        )

    def _next_improve() -> NextOut:
        with db.connect() as conn:
            weakest = db.get_weakest_patterns(conn, n=1, attempted_only=True)
            if not weakest:
                # cold start: no attempts yet — fall back to a sensible warmup
                # pattern at the user's default Elo.
                fallback = "hashing"
                from lc_coach.mastery import INITIAL_PATTERN_ELO
                pattern_elo = INITIAL_PATTERN_ELO
                pattern_name = fallback
                fallback_msg = (
                    f"no attempts yet — defaulting to '{fallback}' as a warmup pattern"
                )
            else:
                row = weakest[0]
                pattern_name = row["name"]
                pattern_elo = float(row["elo"]) if row.get("elo") is not None else 1200.0
                fallback_msg = None

            candidates = db.get_problems_by_pattern(conn, pattern_name, limit=200)
            due_rows = db.get_due_problems(conn, limit=1000)
            due_slugs = {r["problem_slug"] for r in due_rows}
            recent_slugs = db.get_recent_attempt_slugs(conn, days=7)

        if not candidates:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no problems tagged with '{pattern_name}' in the DB. "
                    "Run /ingest to populate."
                ),
            )

        chosen = pick_skill_next(
            candidates,
            pattern_elo=pattern_elo,
            due_slugs=due_slugs,
            recent_slugs=recent_slugs,
            pattern_name=pattern_name,
        )
        if chosen is None:
            raise HTTPException(status_code=404, detail="no candidates")

        rationale = " · ".join(chosen.rationale_parts)
        if fallback_msg:
            rationale = fallback_msg + " · " + rationale
        else:
            rationale = (
                f"improve mode: targeting your weakest pattern '{pattern_name}' "
                f"(Elo {pattern_elo:.0f}) · " + rationale
            )

        return NextOut(
            slug=chosen.slug,
            title=chosen.title,
            difficulty=chosen.difficulty,
            leetcode_url=f"https://leetcode.com/problems/{chosen.slug}/",
            score=chosen.score,
            from_company="(improve mode)",
            rationale=rationale,
            cold_start_used=False,
            target=pattern_name,
            target_pool_size=len(candidates),
            similar_companies=[],
            user_weak_patterns=[pattern_name],
            mode="improve",
            pattern=pattern_name,
            pattern_elo=pattern_elo,
        )

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
    level: int = Field(..., ge=1, le=4)
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


class ReviewOut(BaseModel):
    problem_slug: str
    quality: int
    ease: float
    repetitions: int
    interval_days: int
    due_date: str


class AttemptDoneOut(BaseModel):
    attempt: AttemptOut
    mastery_updates: list[MasteryUpdate] = Field(default_factory=list)
    review: Optional[ReviewOut] = None


class PatternMastery(BaseModel):
    id: int
    name: str
    elo: Optional[float] = None
    n_attempts: int = 0
    last_updated: Optional[str] = None


class IngestIn(BaseModel):
    companies: Optional[list[str]] = None  # canonical names; None = all


class IngestOut(BaseModel):
    companies: int
    problems: int
    requested: Optional[list[str]] = None


class CompanyRow(BaseModel):
    name: str
    last_ingested_at: Optional[str] = None
    n_problems: int = 0
    total_confidence: Optional[float] = None


class CompanyProblemRow(BaseModel):
    problem_slug: str
    leetcode_id: Optional[int] = None
    title: Optional[str] = None
    difficulty: Optional[str] = None
    appearances: list[list[str]] = Field(default_factory=list)
    confidence: float


class DueRow(BaseModel):
    problem_slug: str
    title: Optional[str] = None
    difficulty: Optional[str] = None
    due_date: Optional[str] = None
    interval_days: int
    repetitions: int
    ease: float
    last_quality: Optional[int] = None
    last_reviewed_at: Optional[str] = None


class ReviewCodeIn(BaseModel):
    slug: str
    code: str
    language: Optional[str] = None
    model: Optional[str] = None


class ReviewCodeOut(BaseModel):
    slug: str
    response: str


class MockIn(BaseModel):
    slug: str
    model: Optional[str] = None


class MockOut(BaseModel):
    slug: str
    response: str


class SimilarCompany(BaseModel):
    name: str
    score: float
    n_problems: int


class NextOut(BaseModel):
    slug: str
    title: Optional[str] = None
    difficulty: Optional[str] = None
    leetcode_url: str
    score: float
    from_company: str
    rationale: str
    cold_start_used: bool
    target: str
    target_pool_size: int
    similar_companies: list[SimilarCompany] = Field(default_factory=list)
    user_weak_patterns: list[str] = Field(default_factory=list)
    mode: str = "company"
    pattern: Optional[str] = None
    pattern_elo: Optional[float] = None


# Module-level instance for `uvicorn lc_coach.app:app`
app = create_app()
