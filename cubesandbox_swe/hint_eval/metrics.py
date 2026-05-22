"""Offline hint-invariant metrics."""

from __future__ import annotations

import math
from statistics import mean
from typing import Any


EPS = 1e-12


def cross_entropy(target: dict[str, float], probs: dict[str, float]) -> float:
    return -sum(float(weight) * math.log(max(EPS, float(probs.get(label, 0.0)))) for label, weight in target.items())


def probe_metrics(
    score_record: dict[str, Any],
    *,
    lambda_: float = 0.5,
    mu: float = 0.25,
    nu: float = 0.25,
) -> dict[str, Any]:
    target = {str(key): float(value) for key, value in score_record["target_distribution"].items()}
    scores = score_record["condition_scores"]
    l0 = cross_entropy(target, scores["neutral"])
    l_plus = cross_entropy(target, scores["causal"])
    l_irrelevant = cross_entropy(target, scores["irrelevant"])
    l_misleading = cross_entropy(target, scores["misleading"])
    g_plus = l0 - l_plus
    s_irrelevant = abs(l_irrelevant - l0)
    h_misleading = l_misleading - l0
    burden = l0 + lambda_ * max(0.0, g_plus) + mu * s_irrelevant + nu * max(0.0, h_misleading)
    return {
        "probe_id": score_record["probe_id"],
        "task_id": score_record.get("task_id"),
        "instance_id": score_record.get("instance_id"),
        "model": score_record.get("model"),
        "scorer": score_record.get("scorer"),
        "cutpoint_type": score_record.get("cutpoint_type"),
        "L0": l0,
        "L_plus": l_plus,
        "L_irrelevant": l_irrelevant,
        "L_misleading": l_misleading,
        "G_plus": g_plus,
        "S_irrelevant": s_irrelevant,
        "H_misleading": h_misleading,
        "B": burden,
    }


def aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    metric_names = ["L0", "L_plus", "L_irrelevant", "L_misleading", "G_plus", "S_irrelevant", "H_misleading", "B"]
    if not rows:
        return {"count": 0, **{name: 0.0 for name in metric_names}}
    return {"count": len(rows), **{name: mean(float(row[name]) for row in rows) for name in metric_names}}


def grouped_aggregates(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = row.get(key)
        group_key = "null" if value is None else str(value)
        groups.setdefault(group_key, []).append(row)
    return [{key: group_key, **aggregate_metrics(group_rows)} for group_key, group_rows in sorted(groups.items())]


def ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    out = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            out[indexed[k][0]] = rank
        i = j
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = mean(xs)
    my = mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denom_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    denom_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denom_x == 0 or denom_y == 0:
        return None
    return numerator / (denom_x * denom_y)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    return pearson(ranks(xs), ranks(ys))


def kendall_tau(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    concordant = 0
    discordant = 0
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            dx = (xs[i] > xs[j]) - (xs[i] < xs[j])
            dy = (ys[i] > ys[j]) - (ys[i] < ys[j])
            if dx == 0 or dy == 0:
                continue
            if dx == dy:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return None
    return (concordant - discordant) / total
