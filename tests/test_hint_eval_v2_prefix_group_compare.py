from __future__ import annotations

from pathlib import Path

import pytest

from cubesandbox_swe.hint_eval.io import write_jsonl
from cubesandbox_swe.hint_eval.v2 import compare_prefix_groups


FIXTURES = "tests/fixtures/hint_eval_v2"


def score_record(
    probe_id: str,
    instance_id: str,
    prefix_source: str,
    support_bucket: str,
    resolved: bool,
    target_prob_neutral: float,
) -> dict:
    return {
        "schema_version": "hint_eval_score_v1",
        "probe_id": probe_id,
        "prefix_id": f"{probe_id}:prefix",
        "task_id": None,
        "instance_id": instance_id,
        "model": "fake-model",
        "scorer": "fake",
        "cutpoint_type": "file_localization",
        "prefix_source": prefix_source,
        "support_bucket": support_bucket,
        "prefix_group": "high_support_teacher_prefix" if prefix_source == "teacher_success" else "student_success_prefix",
        "oracle_source": "teacher_future",
        "trajectory_resolved": resolved,
        "target_distribution": {"A": 1.0},
        "candidate_actions": [],
        "candidate_diagnostics": {"candidate_count": 4, "positive_count": 1, "candidate_kind": "mixed"},
        "hints": {
            "neutral": "n",
            "causal": "c",
            "irrelevant": "i",
            "misleading": "m",
        },
        "condition_scores": {
            "neutral": {"A": target_prob_neutral, "B": 1 - target_prob_neutral},
            "causal": {"A": min(0.99, target_prob_neutral + 0.05), "B": max(0.01, 0.95 - target_prob_neutral)},
            "irrelevant": {"A": target_prob_neutral, "B": 1 - target_prob_neutral},
            "misleading": {"A": max(0.01, target_prob_neutral - 0.05), "B": min(0.99, 1.05 - target_prob_neutral)},
        },
    }


def test_compare_prefix_groups_uses_goodness_direction(tmp_path: Path) -> None:
    scores = tmp_path / "scores.jsonl"
    output = tmp_path / "comparison.json"
    markdown = tmp_path / "comparison.md"
    write_jsonl(
        scores,
        [
            score_record("p-low-b", "example__repo-202", "student_onpolicy", "high", True, 0.9),
            score_record("p-high-b", "example__repo-201", "student_onpolicy", "high", False, 0.2),
            score_record("p-teacher", "example__repo-201", "teacher_success", "high", True, 0.3),
        ],
    )

    comparison = compare_prefix_groups(
        scores_path=scores,
        online_globs=[f"{FIXTURES}/online_*.json"],
        output=output,
        markdown=markdown,
    )

    student = next(group for group in comparison["groups"] if group["prefix_group"] == "all_student_onpolicy_prefix")
    assert student["correlations"]["spearman_goodness_online"] == pytest.approx(1.0)
    assert output.exists()
    assert "Goodness = -B" in markdown.read_text(encoding="utf-8")
