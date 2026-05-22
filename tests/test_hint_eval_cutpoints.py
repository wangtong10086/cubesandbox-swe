from __future__ import annotations

from pathlib import Path

from cubesandbox_swe.hint_eval.cutpoints import select_cutpoints
from cubesandbox_swe.hint_eval.trajectory import load_trajectory


FIXTURE = Path("tests/fixtures/hint_eval/sample_trajectory_success.json")


def test_select_cutpoints_from_fixture() -> None:
    trajectory = load_trajectory(FIXTURE)
    cutpoints = select_cutpoints(trajectory.attempts[0], max_cutpoints=4)

    assert [cutpoint.cutpoint_type for cutpoint in cutpoints] == [
        "file_localization",
        "edit_decision",
        "verification",
    ]
    assert all(cutpoint.reason for cutpoint in cutpoints)
