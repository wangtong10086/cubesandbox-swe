from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

import pytest

from cubesandbox_swe.hint_eval.metrics import probe_metrics
from cubesandbox_swe.hint_eval.sensitivity_capability import RunSpec, analyze_runs, render_report


def test_analyze_runs_computes_status_counts_and_task_correlations(tmp_path: Path) -> None:
    spec = write_fixture_run(tmp_path, key="run1", label="Fixture Model", helpful_first=True)

    analysis = analyze_runs([spec], raw_task_limit=10, raw_probe_limit=10)

    run = analysis["runs"][0]
    assert run["online_success_rate"] == pytest.approx(0.5)
    assert run["status_counts"] == {"no_patch": 1, "ok": 1}
    assert run["aggregate"]["mean_abs_G_plus"] > 0
    assert run["task_level_correlations"]["G_plus"]["n"] == 2
    assert run["raw_task_rows"][0]["probe_count"] == 1


def test_render_report_contains_model_matrix_and_raw_sections(tmp_path: Path) -> None:
    specs = [
        write_fixture_run(tmp_path, key="qwen36", label="Qwen3.6 27B repaired", helpful_first=True),
        write_fixture_run(tmp_path, key="affine", label="Affine", helpful_first=False),
        write_fixture_run(tmp_path, key="qwen32", label="Qwen3 32B", helpful_first=True),
    ]

    report = render_report(analyze_runs(specs, raw_task_limit=2, raw_probe_limit=2))

    assert "Model x Hint" in report
    assert "Qwen3.6 27B repaired" in report
    assert "Affine" in report
    assert "Qwen3 32B" in report
    assert "Raw task-level rows" in report
    assert "最大 hint 变化 probes" in report
    assert "```mermaid" in report


def write_fixture_run(tmp_path: Path, *, key: str, label: str, helpful_first: bool) -> RunSpec:
    root = tmp_path / key
    root.mkdir()
    scores_path = root / "scores.jsonl"
    summary_path = root / "summary.json"
    ablation_path = root / "ablation.json"
    prefix_group_path = root / "prefix_group.json"

    records = [
        score_record(
            probe_id=f"{key}-p1",
            task_id=1,
            instance_id=f"{key}-task-1",
            neutral_prob=0.4 if helpful_first else 0.8,
            causal_prob=0.8 if helpful_first else 0.4,
        ),
        score_record(
            probe_id=f"{key}-p2",
            task_id=2,
            instance_id=f"{key}-task-2",
            neutral_prob=0.8 if helpful_first else 0.4,
            causal_prob=0.4 if helpful_first else 0.8,
        ),
    ]
    write_jsonl(scores_path, records)
    metrics = [probe_metrics(record) for record in records]
    write_jsonl(
        root / "online_results.jsonl",
        [
            online_record(task_id=1, instance_id=f"{key}-task-1", status="ok", score=1.0),
            online_record(task_id=2, instance_id=f"{key}-task-2", status="no_patch", score=0.0),
        ],
    )
    write_json(
        summary_path,
        {
            "aggregate": aggregate(metrics),
            "probe_count": len(records),
            "score_count": len(records),
            "online": {"joined_probe_count": len(records)},
        },
    )
    write_json(
        ablation_path,
        {
            "joined_count": len(records),
            "baselines": [
                ablation_row("Goodness=-B", 0.25),
                ablation_row("G_plus", 0.5 if helpful_first else -0.5),
            ],
        },
    )
    write_json(
        prefix_group_path,
        {
            "groups": [
                {
                    "prefix_group": "student_failure_prefix",
                    "probe_count": len(records),
                    "task_count": 2,
                    "joined_count": 2,
                    "online_resolve_rate": 0.5,
                    **aggregate(metrics),
                    "spearman_goodness_online": 0.25,
                    "pairwise_accuracy": 1.0,
                }
            ]
        },
    )
    return RunSpec(
        key=key,
        label=label,
        model=label,
        root=root,
        scores_path=scores_path,
        summary_path=summary_path,
        ablation_path=ablation_path,
        prefix_group_path=prefix_group_path,
    )


def score_record(
    *,
    probe_id: str,
    task_id: int,
    instance_id: str,
    neutral_prob: float,
    causal_prob: float,
) -> dict[str, object]:
    return {
        "probe_id": probe_id,
        "prefix_id": f"{probe_id}-prefix",
        "task_id": task_id,
        "instance_id": instance_id,
        "model": "fixture-model",
        "scorer": "fixture",
        "cutpoint_type": "file_localization",
        "prefix_source": "student_onpolicy",
        "support_bucket": "high",
        "prefix_group": "student_failure_prefix",
        "oracle_source": "fixture",
        "trajectory_resolved": False,
        "target_distribution": {"A": 1.0},
        "condition_scores": {
            "neutral": {"A": neutral_prob, "B": 1 - neutral_prob},
            "causal": {"A": causal_prob, "B": 1 - causal_prob},
            "irrelevant": {"A": neutral_prob, "B": 1 - neutral_prob},
            "misleading": {"A": 0.2, "B": 0.8},
        },
    }


def online_record(*, task_id: int, instance_id: str, status: str, score: float) -> dict[str, object]:
    return {
        "schema_version": "hint_eval_online_result_v1",
        "task_id": task_id,
        "instance_id": instance_id,
        "score": score,
        "resolved": score > 0,
        "status": status,
    }


def aggregate(rows: list[dict[str, object]]) -> dict[str, float]:
    keys = ["L0", "L_plus", "G_plus", "S_irrelevant", "H_misleading", "B", "Goodness"]
    return {key: mean(float(row[key]) for row in rows) for key in keys}


def ablation_row(metric: str, spearman: float) -> dict[str, object]:
    return {
        "metric": metric,
        "spearman": spearman,
        "kendall": spearman / 2,
        "pearson": spearman / 3,
        "pairwise_accuracy": 0.5 + spearman / 10,
        "bootstrap_ci": {"spearman": {"low": spearman - 0.1, "high": spearman + 0.1}},
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
