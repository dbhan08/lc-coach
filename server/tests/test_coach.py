import subprocess
from types import SimpleNamespace

import pytest

from lc_coach import coach


def test_build_hint_prompt_levels():
    for level in (1, 2, 3):
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
            title="x", statement="y", difficulty=None, level=4, prior_hints=[]
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
