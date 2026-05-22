from __future__ import annotations

import json
from pathlib import Path

from cubesandbox_swe.cli import main


FIXTURE = "tests/fixtures/hint_eval/sample_trajectory_success.json"
ONLINE = "tests/fixtures/hint_eval/sample_online_result.json"


def test_hint_eval_help_commands(capsys) -> None:
    assert main(["hint-eval", "--help"]) == 0
    assert "build" in capsys.readouterr().out

    for command in ("build", "score", "analyze", "report"):
        assert main(["hint-eval", command, "--help"]) == 0
        assert f"hint-eval {command}" in capsys.readouterr().out


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
