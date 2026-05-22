"""Trajectory and rollout-bucket record helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_rollout_task_id(value: Any) -> int | str | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text) if text.isdigit() else text


def conversation_from_trajectory(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    conversation: list[dict[str, Any]] = []
    for attempt in trajectory.get("attempts", []):
        prompt = attempt.get("prompt")
        if prompt:
            conversation.append({"role": "user", "content": prompt})
        for item in attempt.get("codex_events", []):
            if isinstance(item, dict):
                conversation.append(item)
    return conversation


def build_rollout_bucket_record(
    result: dict[str, Any],
    task: dict[str, Any],
    trajectory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the local Affine rollout-bucket compatible record."""
    trajectory = trajectory or {}
    verify = result.get("verify") or {}
    test_stats = verify.get("test_stats") or {}
    task_id = parse_rollout_task_id(result.get("rollout_task_id") or task.get("task_id"))

    return {
        "miner_hotkey": result.get("rollout_miner_hotkey") or "local-cubesandbox",
        "model_revision": result.get("rollout_model_revision") or result.get("run_id") or "",
        "model": result.get("rollout_model") or result.get("model") or "",
        "env": "SWE-INFINITE",
        "task_id": task_id,
        "score": verify.get("score"),
        "latency_ms": result.get("latency_ms"),
        "timestamp": result.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "validator_hotkey": result.get("rollout_validator_hotkey") or "executor-SWE-INFINITE-local",
        "block_number": int(result.get("rollout_block_number") or 0),
        "signature": result.get("rollout_signature") or "",
        "extra": {
            "task_type": "SWE-INFINITE",
            "agent_type": "codex",
            "instance_id": task.get("instance_id"),
            "repo": task.get("repo"),
            "problem_statement": task.get("problem_statement"),
            "fix_patch": result.get("fix_patch") or result.get("patch"),
            "conversation": conversation_from_trajectory(trajectory),
            "model_calls": result.get("model_calls"),
            "total_tokens": result.get("total_tokens"),
            "test_stats": test_stats,
            "usage": result.get("usage") or {},
            "cubesandbox": {
                "solve_template": result.get("solve_template"),
                "verify_template": result.get("verify_template"),
                "trajectory_schema_version": trajectory.get("schema_version"),
                "save_restore": {
                    "state_after_save": verify.get("state_after_save"),
                    "state_after_restore": verify.get("state_after_restore"),
                },
            },
        },
    }


def redact_text(text: str, secrets: dict[str, str | None]) -> str:
    redacted = text
    for key, value in secrets.items():
        if value:
            redacted = redacted.replace(value, f"<redacted:{key}>")
    return redacted
