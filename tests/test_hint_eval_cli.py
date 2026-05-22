from __future__ import annotations

import json
from pathlib import Path

from cubesandbox_swe.cli import main


FIXTURE = "tests/fixtures/hint_eval/sample_trajectory_success.json"
ONLINE = "tests/fixtures/hint_eval/sample_online_result.json"


def test_hint_eval_help_commands(capsys) -> None:
    assert main(["hint-eval", "--help"]) == 0
    assert "build" in capsys.readouterr().out

    for command in ("build", "score", "score-batch", "provider-check", "analyze", "report", "ablate"):
        assert main(["hint-eval", command, "--help"]) == 0
        assert f"hint-eval {command}" in capsys.readouterr().out

    for command in ("collect-prefixes", "support", "build-onpolicy", "compare-prefix-groups"):
        assert main(["hint-eval", command, "--help"]) == 0
        assert f"hint-eval {command}" in capsys.readouterr().out

    assert main(["hint-eval", "export", "online-results", "--help"]) == 0
    assert "hint-eval export online-results" in capsys.readouterr().out


def test_fixture_pipeline_build_score_analyze_report(tmp_path: Path) -> None:
    probes = tmp_path / "probes.jsonl"
    scores = tmp_path / "scores.fake.jsonl"
    summary = tmp_path / "summary.json"
    report = tmp_path / "report.md"

    assert main(
        [
            "hint-eval",
            "build",
            "--trajectory-glob",
            FIXTURE,
            "--output",
            str(probes),
            "--max-cutpoints-per-trajectory",
            "4",
            "--seed",
            "0",
        ]
    ) == 0

    probe_rows = [json.loads(line) for line in probes.read_text(encoding="utf-8").splitlines()]
    assert probe_rows
    assert set(probe_rows[0]["hints"]) == {"neutral", "causal", "irrelevant", "misleading"}
    assert abs(sum(probe_rows[0]["target_distribution"].values()) - 1.0) < 1e-9

    assert main(
        [
            "hint-eval",
            "score",
            "--probes",
            str(probes),
            "--output",
            str(scores),
            "--scorer",
            "fake",
            "--model",
            "fake-model",
        ]
    ) == 0
    first_score = scores.read_text(encoding="utf-8")

    assert main(
        [
            "hint-eval",
            "score",
            "--probes",
            str(probes),
            "--output",
            str(scores),
            "--scorer",
            "fake",
            "--model",
            "fake-model",
        ]
    ) == 0
    assert scores.read_text(encoding="utf-8") == first_score

    assert main(
        [
            "hint-eval",
            "analyze",
            "--scores",
            str(scores),
            "--online-results-glob",
            ONLINE,
            "--output",
            str(summary),
        ]
    ) == 0
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["probe_count"] == len(probe_rows)
    assert payload["aggregate"]["B"] > 0

    assert main(
        [
            "hint-eval",
            "report",
            "--summary",
            str(summary),
            "--scores",
            str(scores),
            "--output",
            str(report),
        ]
    ) == 0
    assert "# Hint-Invariant SWE Offline Evaluation" in report.read_text(encoding="utf-8")


def test_provider_check_writes_status(tmp_path: Path) -> None:
    output = tmp_path / "provider.json"

    assert main(
        [
            "hint-eval",
            "provider-check",
            "--output",
            str(output),
            "--scorer",
            "fake",
            "--model",
            "fake-model",
        ]
    ) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"


def test_score_batch_uses_cache_and_resume(tmp_path: Path) -> None:
    probes = tmp_path / "probes.jsonl"
    scores = tmp_path / "scores.jsonl"
    cache = tmp_path / "cache"
    probes.write_text(
        json.dumps(
            {
                "probe_id": "p1",
                "task_id": 1,
                "instance_id": "repo__name-1",
                "trajectory_file": "trajectory.json",
                "cutpoint_type": "file_localization",
                "prefix_group": "old_group",
                "trajectory_resolved": False,
                "prefix_messages": [],
                "candidate_actions": [
                    {"id": "A", "kind": "operation", "label": "A", "text": "inspect source", "is_positive": True, "weight": 1.0},
                    {"id": "B", "kind": "operation", "label": "B", "text": "inspect docs", "is_positive": False, "weight": 0.0},
                ],
                "target_distribution": {"A": 1.0},
                "hints": {
                    "neutral": "No additional diagnostic note.",
                    "causal": "Additional diagnostic note: inspect source.",
                    "irrelevant": "Additional diagnostic note: docs exist.",
                    "misleading": "Additional diagnostic note: inspect docs.",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = [
        "hint-eval",
        "score-batch",
        "--probes",
        str(probes),
        "--output",
        str(scores),
        "--scorer",
        "fake",
        "--model",
        "fake-model",
        "--concurrency",
        "2",
        "--cache-dir",
        str(cache),
        "--resume",
    ]
    assert main(args) == 0
    probe_payload = json.loads(probes.read_text(encoding="utf-8"))
    probe_payload["prefix_group"] = "new_group"
    probe_payload["trajectory_resolved"] = True
    probes.write_text(json.dumps(probe_payload) + "\n", encoding="utf-8")
    assert main(args) == 0

    rows = [json.loads(line) for line in scores.read_text(encoding="utf-8").splitlines()]
    stats = json.loads((tmp_path / "scores.jsonl.stats.json").read_text(encoding="utf-8"))
    assert [row["probe_id"] for row in rows] == ["p1"]
    assert rows[0]["prefix_group"] == "new_group"
    assert rows[0]["trajectory_resolved"] is True
    assert stats["status"] == "ok"


def test_export_online_results_and_ablate(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(
            {
                "task_json": "results/swe50_trajectories/tasks/task_00000011827.json",
                "instance_id": "sparfenyuk__mcp-proxy-2",
                "model": "Qwen/Qwen3.6-27B",
                "status": "ok",
                "verify": {"status": "ok", "score": 1.0},
            }
        ),
        encoding="utf-8",
    )
    failed_result = tmp_path / "failed_result.json"
    failed_result.write_text(
        json.dumps(
            {
                "task_json": "results/swe50_trajectories/tasks/task_00000011828.json",
                "instance_id": "example__missing-score",
                "model": "Qwen/Qwen3.6-27B",
                "status": "failed",
            }
        ),
        encoding="utf-8",
    )
    online = tmp_path / "online.jsonl"
    assert (
        main(
            [
                "hint-eval",
                "export",
                "online-results",
                "--input-glob",
                str(tmp_path / "*result.json"),
                "--output",
                str(online),
            ]
        )
        == 0
    )
    online_rows = [json.loads(line) for line in online.read_text(encoding="utf-8").splitlines()]
    by_task = {row["task_id"]: row for row in online_rows}
    assert by_task[11827]["resolved"] is True
    assert by_task[11828]["score"] is None
    assert "missing_verify_score" in by_task[11828]["warnings"]

    scores = tmp_path / "scores.jsonl"
    scores.write_text(
        json.dumps(
            {
                "schema_version": "hint_eval_score_v1",
                "probe_id": "p1",
                "task_id": 11827,
                "instance_id": "sparfenyuk__mcp-proxy-2",
                "model": "fake-model",
                "scorer": "fake",
                "cutpoint_type": "file_localization",
                "target_distribution": {"A": 1.0},
                "candidate_actions": [],
                "hints": {"neutral": "n", "causal": "c", "irrelevant": "i", "misleading": "m"},
                "condition_scores": {
                    "neutral": {"A": 0.7, "B": 0.3},
                    "causal": {"A": 0.8, "B": 0.2},
                    "irrelevant": {"A": 0.7, "B": 0.3},
                    "misleading": {"A": 0.6, "B": 0.4},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ablation = tmp_path / "ablation.json"
    markdown = tmp_path / "ablation.md"
    assert (
        main(
            [
                "hint-eval",
                "ablate",
                "--scores",
                str(scores),
                "--online-results-glob",
                str(online),
                "--output",
                str(ablation),
                "--markdown",
                str(markdown),
                "--bootstrap-samples",
                "5",
            ]
        )
        == 0
    )
    payload = json.loads(ablation.read_text(encoding="utf-8"))
    assert payload["joined_count"] == 1
    assert "Goodness = -B" in markdown.read_text(encoding="utf-8")
