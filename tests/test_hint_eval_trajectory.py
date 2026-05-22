from __future__ import annotations

import json
from pathlib import Path

from cubesandbox_swe.hint_eval.trajectory import derive_task_id, extract_tool_actions, load_trajectory, normalize_repo_path


def test_extract_tool_actions_supports_legacy_codex_events() -> None:
    events = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/usr/bin/zsh -lc \"sed -n '1,80p' src/pkg/module.py\"",
                "aggregated_output": "contents",
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "file_change",
                "changes": [
                    {
                        "path": "/tmp/run/app/src/pkg/module.py",
                        "kind": "update",
                    }
                ],
                "status": "completed",
            },
        },
    ]

    actions = extract_tool_actions(events)

    assert [action.tool for action in actions] == ["cube_run", "cube_apply_patch"]
    assert actions[0].command == "/usr/bin/zsh -lc \"sed -n '1,80p' src/pkg/module.py\""
    assert actions[0].result_text == "contents"
    assert actions[1].path == "src/pkg/module.py"


def test_normalize_repo_path_strips_host_run_app_prefix() -> None:
    assert normalize_repo_path("/host/results/run/app/src/pkg/module.py") == "src/pkg/module.py"


def test_derive_task_id_from_task_json_filename(tmp_path: Path) -> None:
    trajectory_path = tmp_path / "cubesandbox_codex_trajectory_qwen-retry.json"
    trajectory_path.write_text(
        json.dumps(
            {
                "task": {
                    "task_json": "results/swe50_trajectories/tasks/task_00000011827.json",
                    "instance_id": "sparfenyuk__mcp-proxy-2",
                },
                "attempts": [],
            }
        ),
        encoding="utf-8",
    )

    assert derive_task_id(load_trajectory(trajectory_path)) == 11827


def test_derive_task_id_from_trajectory_path(tmp_path: Path) -> None:
    trajectory_path = tmp_path / "runs" / "11827" / "rep_0" / "cubesandbox_codex_trajectory_latest.json"
    trajectory_path.parent.mkdir(parents=True)
    trajectory_path.write_text(json.dumps({"task": {"instance_id": "sparfenyuk__mcp-proxy-2"}, "attempts": []}), encoding="utf-8")

    assert derive_task_id(load_trajectory(trajectory_path)) == 11827
