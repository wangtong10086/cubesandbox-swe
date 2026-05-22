"""Analysis for scored hint probes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import expand_globs, read_json, read_jsonl
from .metrics import aggregate_metrics, grouped_aggregates, kendall_tau, probe_metrics, spearman


def analyze_scores(
    scores_path: str | Path,
    *,
    online_result_globs: list[str] | None = None,
    lambda_: float = 0.5,
    mu: float = 0.25,
    nu: float = 0.25,
) -> dict[str, Any]:
    score_records = read_jsonl(scores_path)
    probe_rows = [probe_metrics(record, lambda_=lambda_, mu=mu, nu=nu) for record in score_records]
    online_scores = load_online_scores(online_result_globs or [])
    joined = join_online_scores(probe_rows, online_scores)
    correlations = compute_correlations(joined)
    return {
        "schema_version": "hint_eval_summary_v1",
        "scores_file": str(scores_path),
        "score_count": len(score_records),
        "probe_count": len(probe_rows),
        "trajectory_count": len({record.get("trajectory_file") for record in score_records if record.get("trajectory_file")}),
        "model_count": len({record.get("model") for record in score_records if record.get("model")}),
        "scorers": sorted({str(record.get("scorer")) for record in score_records if record.get("scorer")}),
        "models": sorted({str(record.get("model")) for record in score_records if record.get("model")}),
        "weights": {"lambda": lambda_, "mu": mu, "nu": nu},
        "aggregate": aggregate_metrics(probe_rows),
        "by_task": grouped_aggregates(probe_rows, "task_id"),
        "by_model": grouped_aggregates(probe_rows, "model"),
        "by_cutpoint_type": grouped_aggregates(probe_rows, "cutpoint_type"),
        "online": {
            "result_count": len(online_scores),
            "joined_probe_count": len(joined),
            "correlations": correlations,
        },
        "probes": probe_rows,
    }


def load_online_scores(patterns: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for path in expand_globs(patterns):
        try:
            payload = read_json(path)
        except Exception:
            continue
        key = online_key(payload)
        score = online_score(payload)
        if key is not None and score is not None:
            scores[key] = score
    return scores


def online_key(payload: dict[str, Any]) -> str | None:
    for key in (payload.get("instance_id"), payload.get("task_id")):
        if key is not None:
            return str(key)
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    for key in (task.get("instance_id"), task.get("task_id"), extra.get("instance_id"), extra.get("task_id")):
        if key is not None:
            return str(key)
    return None


def online_score(payload: dict[str, Any]) -> float | None:
    candidates = [
        payload.get("score"),
        (payload.get("verify") or {}).get("score") if isinstance(payload.get("verify"), dict) else None,
        (payload.get("verify_result") or {}).get("score") if isinstance(payload.get("verify_result"), dict) else None,
    ]
    for value in candidates:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    return None


def join_online_scores(probe_rows: list[dict[str, Any]], online_scores: dict[str, float]) -> list[dict[str, float]]:
    joined: list[dict[str, float]] = []
    for row in probe_rows:
        keys = [row.get("instance_id"), row.get("task_id")]
        for key in keys:
            if key is not None and str(key) in online_scores:
                joined.append({"offline_B": float(row["B"]), "online_score": online_scores[str(key)]})
                break
    return joined


def compute_correlations(joined: list[dict[str, float]]) -> dict[str, Any]:
    if len(joined) < 2:
        return {
            "status": "insufficient_data",
            "spearman": None,
            "kendall": None,
            "pairwise_ranking_accuracy": None,
        }
    offline = [row["offline_B"] for row in joined]
    online = [row["online_score"] for row in joined]
    spearman_value = spearman(offline, online)
    kendall_value = kendall_tau(offline, online)
    ranking_value = pairwise_ranking_accuracy(offline, online)
    status = "ok" if any(value is not None for value in (spearman_value, kendall_value, ranking_value)) else "insufficient_data"
    return {
        "status": status,
        "spearman": spearman_value,
        "kendall": kendall_value,
        "pairwise_ranking_accuracy": ranking_value,
    }


def pairwise_ranking_accuracy(offline: list[float], online: list[float]) -> float | None:
    correct = 0
    total = 0
    for i in range(len(offline)):
        for j in range(i + 1, len(offline)):
            do = (offline[i] < offline[j]) - (offline[i] > offline[j])
            oo = (online[i] < online[j]) - (online[i] > online[j])
            if do == 0 or oo == 0:
                continue
            total += 1
            if do == oo:
                correct += 1
    if total == 0:
        return None
    return correct / total
