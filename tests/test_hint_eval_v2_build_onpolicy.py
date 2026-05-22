from __future__ import annotations

from pathlib import Path

from cubesandbox_swe.hint_eval.v2 import build_onpolicy_probes, collect_prefixes, compute_support


FIXTURES = "tests/fixtures/hint_eval_v2"


def test_build_onpolicy_uses_teacher_oracle_for_failed_student_prefix(tmp_path: Path) -> None:
    prefixes = tmp_path / "prefixes.jsonl"
    support = tmp_path / "support.jsonl"
    probes = tmp_path / "probes.jsonl"
    collect_prefixes(
        teacher_globs=[f"{FIXTURES}/teacher_*.json"],
        student_globs=[f"{FIXTURES}/student_*.json"],
        online_globs=[f"{FIXTURES}/online_*.json"],
        output=prefixes,
        max_prefixes_per_trajectory=4,
        seed=0,
    )
    compute_support(prefixes, output=support, student_model="qwen3.6-27b")

    records = build_onpolicy_probes(
        prefixes_path=prefixes,
        support_path=support,
        output=probes,
        max_candidates=4,
        hint_strength="l2",
        seed=0,
    )

    assert records
    assert {record["schema_version"] for record in records} == {"hint_eval_probe_v2"}
    assert {"teacher_success", "student_onpolicy"} <= {record["prefix_source"] for record in records}
    failed_student = [
        record for record in records
        if record["prefix_source"] == "student_onpolicy" and record["trajectory_resolved"] is False
    ]
    assert failed_student
    assert any(record["oracle_source"] == "teacher_future" for record in failed_student)
    assert all(set(record["hints"]) == {"neutral", "causal", "irrelevant", "misleading"} for record in records)
    assert all(record["candidate_diagnostics"]["candidate_count"] <= 4 for record in records)
