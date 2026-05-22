"""Utilities for parsing Codex CLI JSONL event streams."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class CodexEventSummary:
    total_tokens: int = 0
    model_calls: int = 0
    conversation: list[dict[str, Any]] = field(default_factory=list)
    last_error: str | None = None


def parse_jsonl_events(stdout: str) -> CodexEventSummary:
    """Parse Codex `--json` stdout into usage, conversation, and error data."""
    total_input = 0
    total_output = 0
    model_calls = 0
    conversation: list[dict[str, Any]] = []
    last_error: str | None = None

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")
        if event_type == "turn.completed":
            model_calls += 1
            usage = event.get("usage") or {}
            total_input += int(usage.get("input_tokens") or 0)
            total_output += int(usage.get("output_tokens") or 0)
        elif event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict):
                conversation.append(item)
        elif event_type == "error":
            message = event.get("message")
            if isinstance(message, str) and message.strip():
                last_error = message.strip()
        elif event_type == "turn.failed":
            error = event.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else None
            if isinstance(message, str) and message.strip():
                last_error = message.strip()

    return CodexEventSummary(
        total_tokens=total_input + total_output,
        model_calls=model_calls,
        conversation=conversation,
        last_error=last_error,
    )
