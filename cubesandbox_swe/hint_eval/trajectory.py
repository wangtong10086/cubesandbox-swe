"""Trajectory normalization for hint-eval probe construction."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from .io import read_json


SOURCE_EXTENSIONS = (
    ".py",
    ".rb",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".php",
    ".cs",
    ".swift",
    ".kt",
    ".scala",
    ".vue",
    ".svelte",
)


@dataclass(frozen=True)
class ToolAction:
    index: int
    tool: str
    arguments: dict[str, Any]
    status: str | None = None
    result_text: str = ""

    @property
    def command(self) -> str:
        value = self.arguments.get("command")
        return value if isinstance(value, str) else ""

    @property
    def path(self) -> str:
        value = self.arguments.get("path")
        return normalize_repo_path(value) if isinstance(value, str) else ""

    @property
    def patch(self) -> str:
        value = self.arguments.get("patch")
        return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class AttemptView:
    attempt: int
    prompt: str
    events: list[dict[str, Any]]
    patch: str
    actions: list[ToolAction]


@dataclass(frozen=True)
class TrajectoryView:
    path: Path
    raw: dict[str, Any]
    task: dict[str, Any]
    environment: dict[str, Any]
    attempts: list[AttemptView]


def load_trajectory(path: str | Path) -> TrajectoryView:
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"trajectory must be a JSON object: {path}")
    task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
    environment = raw.get("environment") if isinstance(raw.get("environment"), dict) else raw.get("settings") or {}
    attempts = [normalize_attempt(item) for item in raw.get("attempts", []) if isinstance(item, dict)]
    return TrajectoryView(Path(path), raw, task, environment if isinstance(environment, dict) else {}, attempts)


def normalize_attempt(attempt: dict[str, Any]) -> AttemptView:
    events = attempt.get("codex_events")
    if not isinstance(events, list):
        events = []
    prompt = str(attempt.get("prompt") or "")
    patch = str(attempt.get("patch") or "")
    attempt_no = int(attempt.get("attempt") or 1)
    return AttemptView(
        attempt=attempt_no,
        prompt=prompt,
        events=[event for event in events if isinstance(event, dict)],
        patch=patch,
        actions=extract_tool_actions(events),
    )


def extract_tool_actions(events: list[Any]) -> list[ToolAction]:
    actions: list[ToolAction] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
            continue
        tool = item.get("tool")
        arguments = item.get("arguments")
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            continue
        actions.append(
            ToolAction(
                index=index,
                tool=tool,
                arguments=arguments,
                status=item.get("status") if isinstance(item.get("status"), str) else None,
                result_text=extract_result_text(item),
            )
        )
    return actions


def extract_result_text(item: dict[str, Any]) -> str:
    result = item.get("result")
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for entry in content:
        if isinstance(entry, dict) and isinstance(entry.get("text"), str):
            text = entry["text"]
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                texts.append(text)
            else:
                if isinstance(payload, dict):
                    texts.extend(str(payload.get(key) or "") for key in ("stdout", "stderr") if payload.get(key))
                else:
                    texts.append(text)
    return "\n".join(texts)


def prefix_messages(attempt: AttemptView, before_event_index: int, *, limit: int = 12) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if attempt.prompt:
        messages.append({"role": "user", "content": attempt.prompt})
    for event_index, event in enumerate(attempt.events):
        item = event.get("item") if isinstance(event, dict) else None
        if not isinstance(item, dict):
            continue
        if event_index >= before_event_index:
            break
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            messages.append({"role": "assistant", "content": item["text"]})
        elif item.get("type") == "mcp_tool_call":
            tool = item.get("tool")
            args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            if tool == "cube_read_file":
                messages.append({"role": "tool", "name": tool, "content": f"read {args.get('path', '')}"})
            elif tool == "cube_run":
                messages.append({"role": "tool", "name": tool, "content": str(args.get("command") or "")[:300]})
            elif tool == "cube_apply_patch":
                messages.append({"role": "tool", "name": tool, "content": "apply patch"})
            elif tool == "cube_diff":
                messages.append({"role": "tool", "name": tool, "content": "inspect diff"})
    return messages[-limit:]


def normalize_repo_path(path: str) -> str:
    return path.removeprefix("./").removeprefix("/app/").strip()


def is_source_path(path: str) -> bool:
    clean = normalize_repo_path(path)
    return clean.endswith(SOURCE_EXTENSIONS) and not clean.startswith(".git/")


def command_paths(command: str) -> list[str]:
    candidates = re.findall(r"(?:(?:^|[\s'\":])(?:\./)?)([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+)", command)
    return [normalize_repo_path(path) for path in candidates if "/" in path or is_source_path(path)]


def is_inspect_command(command: str) -> bool:
    lowered = command.lower()
    return any(token in lowered for token in ("sed ", "cat ", "grep ", "rg ", "find ", "nl ", "head ", "tail "))


def is_test_or_verify_command(command: str) -> bool:
    lowered = command.lower()
    return any(
        token in lowered
        for token in ("pytest", "rspec", "npm test", "go test", "cargo test", "mvn test", "--help", "grep -f", "git diff")
    )


def patch_touched_files(patch: str) -> list[str]:
    files: list[str] = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                files.append(normalize_repo_path(parts[2].removeprefix("a/")))
        elif line.startswith("+++ b/"):
            files.append(normalize_repo_path(line.removeprefix("+++ b/")))
    return sorted(dict.fromkeys(path for path in files if path and path != "/dev/null"))
