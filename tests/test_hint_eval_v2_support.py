from __future__ import annotations

from pathlib import Path

from cubesandbox_swe.hint_eval.v2 import collect_prefixes, compute_support


FIXTURES = "tests/fixtures/hint_eval_v2"


def test_support_buckets_teacher_against_student_prefixes(tmp_path: Path) -> None:
    prefixes = tmp_path / "prefixes.jsonl"
    support = tmp_path / "support.jsonl"
    collect_prefixes(
        teacher_globs=[f"{FIXTURES}/teacher_*.json"],
        student_globs=[f"{FIXTURES}/student_*.json"],
        online_globs=[f"{FIXTURES}/online_*.json"],
        output=prefixes,
        max_prefixes_per_trajectory=4,
        seed=0,
    )

    records = compute_support(prefixes, output=support, student_model="qwen3.6-27b")

    teacher = [record for record in records if record["prefix_source"] == "teacher_success"]
    student = [record for record in records if record["prefix_source"] == "student_onpolicy"]
    assert teacher
    assert student
    assert any(record["support_bucket"] in {"high", "medium"} for record in teacher)
    assert all(record["support_bucket"] == "high" for record in student)
    assert all("state_feature_overlap" in record for record in records)
