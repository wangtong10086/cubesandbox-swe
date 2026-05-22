from __future__ import annotations

from cubesandbox_swe.hint_eval.v2 import conclusion_gate


def test_conclusion_gate_level_zero_for_fake_scorer() -> None:
    conclusion = conclusion_gate(
        [{"scorer": "fake", "model": "fake-model"}],
        [{"correlations": {"joined_count": 2}}],
    )

    assert conclusion["level"] == 0
    assert "no model capability claim" in conclusion["strongest_supported_conclusion"]


def test_conclusion_gate_level_two_for_one_real_model_with_online_results() -> None:
    conclusion = conclusion_gate(
        [{"scorer": "choice-logprobs", "model": "qwen3.6-27b"}],
        [{"correlations": {"joined_count": 2}}],
    )

    assert conclusion["level"] == 2
    assert "Task-level prediction" in conclusion["strongest_supported_conclusion"]
