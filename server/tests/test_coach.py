import subprocess
from types import SimpleNamespace

import pytest

from lc_coach import coach


def test_build_hint_prompt_levels():
    for level in (1, 2, 3, 4):
        prompt = coach.build_hint_prompt(
            title="Two Sum",
            statement="Given an array...",
            difficulty="Easy",
            level=level,
            prior_hints=[],
        )
        assert f"LEVEL {level} HINT" in prompt
        assert "Two Sum" in prompt
        assert "Prior hints given: none." in prompt


def test_build_hint_prompt_includes_prior():
    prompt = coach.build_hint_prompt(
        title="Two Sum",
        statement="Given an array...",
        difficulty="Easy",
        level=2,
        prior_hints=[{"level": 1, "response": "this is a hashing problem"}],
    )
    assert "L1: this is a hashing problem" in prompt


def test_build_hint_prompt_invalid_level():
    with pytest.raises(ValueError):
        coach.build_hint_prompt(
            title="x", statement="y", difficulty=None, level=5, prior_hints=[]
        )
    with pytest.raises(ValueError):
        coach.build_hint_prompt(
            title="x", statement="y", difficulty=None, level=0, prior_hints=[]
        )


def test_claude_p_success(monkeypatch):
    fake_completed = SimpleNamespace(returncode=0, stdout="hello world\n", stderr="")
    monkeypatch.setattr(coach, "_resolve_claude_binary", lambda: "/fake/claude")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: fake_completed
    )
    assert coach.claude_p("anything") == "hello world"


def test_claude_p_nonzero_raises(monkeypatch):
    fake_completed = SimpleNamespace(returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr(coach, "_resolve_claude_binary", lambda: "/fake/claude")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_completed)
    with pytest.raises(coach.CoachError, match="boom"):
        coach.claude_p("anything")


def test_claude_p_empty_raises(monkeypatch):
    fake_completed = SimpleNamespace(returncode=0, stdout="   \n", stderr="")
    monkeypatch.setattr(coach, "_resolve_claude_binary", lambda: "/fake/claude")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_completed)
    with pytest.raises(coach.CoachError, match="empty"):
        coach.claude_p("anything")


# --- Prompt contract regressions -----------------------------------------
# These are construction-level contracts: assert the assembled prompt
# string contains (or doesn't contain) certain phrasings. Cheap, deterministic.


def test_l1_template_forbids_data_structure_and_algorithm_in_contract():
    prompt = coach.build_hint_prompt(
        title="X", statement="...", difficulty="Easy", level=1, prior_hints=[]
    )
    # The contract block must explicitly tell Claude not to leak DS/algorithm.
    assert "DO NOT name the specific data structure" in prompt
    assert "DO NOT name the algorithm" in prompt
    assert "DO NOT write code" in prompt
    assert "Tell them ONLY the broad pattern category" in prompt


def test_l2_template_names_data_structure_but_forbids_algorithm():
    prompt = coach.build_hint_prompt(
        title="X", statement="...", difficulty="Easy", level=2, prior_hints=[]
    )
    assert "DATA STRUCTURE(S)" in prompt
    assert "DO NOT name the specific algorithm" in prompt
    assert "DO NOT write code" in prompt


def test_l3_template_strategy_contract():
    prompt = coach.build_hint_prompt(
        title="X", statement="...", difficulty="Easy", level=3, prior_hints=[]
    )
    # L3 is now "Strategy" — prose, not numbered steps, no formal algorithm names
    assert "STRATEGY" in prompt
    assert "DO NOT name the algorithm by formal name" in prompt
    assert "DO NOT decompose into numbered steps" in prompt
    assert "DO NOT write code" in prompt


def test_l4_template_decomposes_but_forbids_code():
    prompt = coach.build_hint_prompt(
        title="X", statement="...", difficulty="Easy", level=4, prior_hints=[]
    )
    assert "Decompose the problem" in prompt
    assert "DO NOT write code" in prompt


def test_review_template_required_fields():
    prompt = coach.build_review_prompt(
        title="Two Sum",
        statement="Given an array...",
        difficulty="Easy",
        code="def two_sum(): pass",
        language="python",
    )
    assert "staff-level engineer" in prompt
    assert "big-O" in prompt or "Big-O" in prompt or "O(" in prompt or "complexity" in prompt
    # The candidate's code must round-trip into the prompt
    assert "def two_sum(): pass" in prompt
    # The reviewer must not be told to write the better solution
    assert "DO NOT write the better solution" in prompt


def test_review_template_handles_no_code():
    prompt = coach.build_review_prompt(
        title="X",
        statement="...",
        difficulty="Easy",
        code="",
        language=None,
    )
    assert "(no code provided)" in prompt
    assert "language: unspecified" in prompt


def test_complexity_template_required_fields():
    prompt = coach.build_complexity_prompt(
        title="Two Sum",
        statement="Given an array...",
        difficulty="Easy",
        code="for i in range(len(nums)): ...",
        language="python",
    )
    assert "Time: O(" in prompt
    assert "Space: O(" in prompt
    # The reviewer must not write a better solution
    assert "DO NOT write better code" in prompt or "DO NOT write code" in prompt
    # And must not name the optimal algorithm
    assert "DO NOT reveal the optimal algorithm by name" in prompt
    # The candidate's code round-trips into the prompt
    assert "for i in range(len(nums)):" in prompt


def test_complexity_template_handles_no_code():
    prompt = coach.build_complexity_prompt(
        title="X",
        statement="...",
        difficulty="Easy",
        code="",
        language=None,
    )
    assert "(no code provided)" in prompt
    assert "language: unspecified" in prompt


def test_mock_template_full_contract_present():
    prompt = coach.build_mock_prompt(
        title="Two Sum", statement="Given an array...", difficulty="Easy"
    )
    # All six interview-round phases should be referenced in the contract
    for phrase in (
        "Pose the problem",
        "Clarifying questions",
        "Approach prompt",
        "Anticipated wrong-direction",
        "Complexity question",
        "follow-up",
    ):
        assert phrase in prompt
    assert "Two Sum" in prompt
