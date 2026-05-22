from __future__ import annotations

from pathlib import Path

import pytest

from cubesandbox_swe.hint_eval.candidates import generate_candidates
from cubesandbox_swe.hint_eval.cutpoints import select_cutpoints
from cubesandbox_swe.hint_eval.trajectory import load_trajectory


FIXTURE = Path("tests/fixtures/hint_eval/sample_trajectory_success.json")


def test_generate_candidates_has_positive_file_and_distribution() -> None:
    attempt = load_trajectory(FIXTURE).attempts[0]
    cutpoint = select_cutpoints(attempt)[0]

    candidates, target, evidence = generate_candidates(attempt, after_event_index=cutpoint.action_index)

    assert candidates
    assert any(candidate.is_positive and candidate.file_path == "src/example.py" for candidate in candidates)
    assert sum(target.values()) == pytest.approx(1.0)
    assert "src/example.py" in evidence
