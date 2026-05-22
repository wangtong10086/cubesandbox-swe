from __future__ import annotations

from cubesandbox_swe.records import build_rollout_bucket_record, parse_rollout_task_id, redact_text


def test_parse_rollout_task_id() -> None:
    assert parse_rollout_task_id("42") == 42
    assert parse_rollout_task_id("abc") == "abc"
    assert parse_rollout_task_id("") is None


def test_build_rollout_bucket_record() -> None:
    record = build_rollout_bucket_record(
        {
            "run_id": "run-1",
            "model": "gpt-5.5",
            "verify": {"score": 1.0, "state_after_save": "paused", "state_after_restore": "running"},
            "solve_template": "solve-tpl",
            "verify_template": "verify-tpl",
            "patch": "diff",
        },
        {"task_id": "7", "instance_id": "repo-7", "repo": "org/repo", "problem_statement": "fix it"},
        {"schema_version": 1, "attempts": [{"prompt": "p", "codex_events": [{"role": "assistant"}]}]},
    )

    assert record["task_id"] == 7
    assert record["score"] == 1.0
    assert record["extra"]["conversation"][0] == {"role": "user", "content": "p"}
    assert record["extra"]["cubesandbox"]["save_restore"]["state_after_restore"] == "running"


def test_redact_text() -> None:
    assert redact_text("secret=abc", {"OPENAI_API_KEY": "abc"}) == "secret=<redacted:OPENAI_API_KEY>"
