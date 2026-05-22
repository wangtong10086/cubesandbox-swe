from __future__ import annotations

from cubesandbox_swe.hint_eval.trajectory import extract_tool_actions, normalize_repo_path


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
