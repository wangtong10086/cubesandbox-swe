from __future__ import annotations

import pytest

from cubesandbox_swe.hint_eval.schemas import CandidateAction, normalize_distribution, require_hint_conditions


def test_normalize_distribution_sums_to_one() -> None:
    dist = normalize_distribution({"A": 2, "B": 2, "C": 0})

    assert dist == {"A": 0.5, "B": 0.5, "C": 0.0}
    assert sum(dist.values()) == pytest.approx(1.0)


def test_require_hint_conditions_rejects_missing() -> None:
    with pytest.raises(ValueError, match="missing hint conditions"):
        require_hint_conditions({"neutral": "x"})


def test_candidate_round_trip_dict() -> None:
    candidate = CandidateAction(
        id="A",
        kind="file",
        label="Inspect source",
        text="inspect src/example.py",
        file_path="src/example.py",
        is_positive=True,
        weight=1.0,
    )

    assert candidate.to_dict()["file_path"] == "src/example.py"
