"""Codex prompt and JSONL helpers used by the CubeSandbox runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass
class CodexResult:
    patch: str
    model_calls: int = 0
    total_tokens: int = 0
    conversation: list[Any] = field(default_factory=list)
    success: bool = True
    error: str | None = None


def build_swe_prompt(
    problem_statement: str,
    repo: str = "",
    language: str = "",
    test_command: str = "",
    fail_to_pass: list[str] | None = None,
) -> str:
    """Wrap a SWE task description into the prompt used by the solver."""
    del test_command, fail_to_pass

    lines = [
        "You are solving a software engineering task. A GitHub repository has an open issue or pull request.",
        "Your goal is to implement the necessary code changes to resolve it.",
        "",
    ]
    if repo:
        lines.append(f"Repository: {repo}")
    if language:
        lines.append(f"Language: {language}")
    lines.extend(
        [
            "",
            "## Issue / PR Description",
            "",
            problem_statement.strip(),
            "",
            "## Instructions",
            "",
            "- Modify ONLY source code files under /app. Do NOT modify tests or config files.",
            "- Read relevant source files to understand the codebase before making changes.",
            "- Make minimal, focused changes that directly address the issue.",
        ]
    )
    return "\n".join(lines)


def parse_codex_json_output(stdout: str) -> tuple[int, int, list[dict[str, Any]], str | None]:
    """Parse Codex ``--json`` JSONL output.

    Returns ``(total_tokens, model_calls, conversation, last_error)``.
    """
    total_input = 0
    total_output = 0
    model_calls = 0
    conversation: list[dict[str, Any]] = []
    last_error: str | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")
        if event_type == "turn.completed":
            model_calls += 1
            usage = event.get("usage", {})
            total_input += int(usage.get("input_tokens") or 0)
            total_output += int(usage.get("output_tokens") or 0)
        elif event_type == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict):
                conversation.append(item)
        elif event_type == "error":
            msg = event.get("message")
            if isinstance(msg, str) and msg.strip():
                last_error = msg.strip()
        elif event_type == "turn.failed":
            err = event.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else None
            if isinstance(msg, str) and msg.strip():
                last_error = msg.strip()

    return total_input + total_output, model_calls, conversation, last_error
