"""LLM coach: shells out to the user's `claude` CLI and assembles prompts.

We deliberately avoid the Anthropic SDK / API key path. The user has a paid
Claude Code subscription and `claude -p "<prompt>"` runs one-shot inference
through that. This keeps the project self-contained and free at point of use.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

DEFAULT_TIMEOUT_SECONDS = 90


class CoachError(RuntimeError):
    """Raised when the underlying `claude` CLI call fails."""


def _resolve_claude_binary() -> str:
    binary = shutil.which("claude")
    if not binary:
        raise CoachError(
            "`claude` CLI not found on PATH. Install Claude Code and ensure it's "
            "authenticated, then make sure `claude` is on your PATH."
        )
    return binary


def claude_p(
    prompt: str,
    *,
    model: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Run `claude -p <prompt>` and return stdout text.

    Raises CoachError on non-zero exit, missing binary, or timeout.
    """
    binary = _resolve_claude_binary()
    args = [binary, "-p", prompt]
    if model:
        args.extend(["--model", model])
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CoachError(f"claude -p timed out after {timeout}s") from exc
    if completed.returncode != 0:
        raise CoachError(
            f"claude -p exited {completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    text = completed.stdout.strip()
    if not text:
        raise CoachError("claude -p returned empty output")
    return text


def _load_template(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _format_prior_hints(prior_hints: list[dict]) -> str:
    if not prior_hints:
        return "Prior hints given: none."
    lines = ["Prior hints already given (do not repeat them, build on them):"]
    for h in prior_hints:
        snippet = h.get("response", "").strip().replace("\n", " ")
        if len(snippet) > 280:
            snippet = snippet[:277] + "..."
        lines.append(f"- L{h.get('level', '?')}: {snippet}")
    return "\n".join(lines)


VALID_HINT_LEVELS = (1, 2, 3, 4)


def build_hint_prompt(
    *,
    title: str,
    statement: str,
    difficulty: Optional[str],
    level: int,
    prior_hints: Optional[list[dict]] = None,
) -> str:
    if level not in VALID_HINT_LEVELS:
        raise ValueError(
            f"hint level must be one of {VALID_HINT_LEVELS} (got {level})"
        )
    template = _load_template(f"hint_level_{level}.txt")
    return template.format(
        title=title,
        difficulty=difficulty or "unknown",
        statement=statement.strip(),
        prior_hints_block=_format_prior_hints(prior_hints or []),
    )


def build_review_prompt(
    *,
    title: str,
    statement: str,
    difficulty: Optional[str],
    code: str,
    language: Optional[str] = None,
) -> str:
    template = _load_template("code_review.txt")
    return template.format(
        title=title,
        difficulty=difficulty or "unknown",
        statement=statement.strip(),
        language=(language or "unspecified").strip(),
        code=code.strip() or "(no code provided)",
    )


def build_mock_prompt(
    *,
    title: str,
    statement: str,
    difficulty: Optional[str],
) -> str:
    template = _load_template("mock_interview.txt")
    return template.format(
        title=title,
        difficulty=difficulty or "unknown",
        statement=statement.strip(),
    )
