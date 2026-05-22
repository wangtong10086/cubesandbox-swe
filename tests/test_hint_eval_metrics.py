from __future__ import annotations

import pytest

from cubesandbox_swe.hint_eval.metrics import cross_entropy, kendall_tau, probe_metrics, spearman


def test_cross_entropy_prefers_target_probability() -> None:
    assert cross_entropy({"A": 1.0}, {"A": 0.9}) < cross_entropy({"A": 1.0}, {"A": 0.1})


def test_probe_metrics_computes_hint_terms() -> None:
    record = {
        "probe_id": "p1",
        "target_distribution": {"A": 1.0},
        "condition_scores": {
            "neutral": {"A": 0.5, "B": 0.5},
            "causal": {"A": 0.8, "B": 0.2},
            "irrelevant": {"A": 0.5, "B": 0.5},
            "misleading": {"A": 0.3, "B": 0.7},
        },
    }

    metrics = probe_metrics(record)

    assert metrics["G_plus"] > 0
    assert metrics["H_misleading"] > 0
    assert metrics["B"] > 0


def test_rank_correlations() -> None:
    assert spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert kendall_tau([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
