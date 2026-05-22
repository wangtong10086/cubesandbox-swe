from __future__ import annotations

from pathlib import Path

from cubesandbox_swe.hint_eval.v2 import collect_prefixes


FIXTURES = "tests/fixtures/hint_eval_v2"


def test_collect_prefixes_distinguishes_sources(tmp_path: Path) -> None:
    output = tmp_path / "prefixes.jsonl"

    records = collect_prefixes(
        teacher_globs=[f"{FIXTURES}/teacher_*.json"],
        student_globs=[f"{FIXTURES}/student_*.json"],
        online_globs=[f"{FIXTURES}/online_*.json"],
        output=output,
        max_prefixes_per_trajectory=4,
        seed=0,
    )

    assert output.exists()
    sources = {record["prefix_source"] for record in records}
    assert sources == {"teacher_success", "student_onpolicy"}
    assert any(record["trajectory_resolved"] is False for record in records if record["prefix_source"] == "student_onpolicy")
    assert all(record["schema_version"] == "hint_eval_prefix_v2" for record in records)
    assert all("abstract_actions" in record["observed_state_features"] for record in records)
    assert len({record["prefix_id"] for record in records}) == len(records)
