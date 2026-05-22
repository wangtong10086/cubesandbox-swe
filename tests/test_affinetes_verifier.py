from __future__ import annotations

import json
import sys
import types

from cubesandbox_swe.legacy.run_affinetes_cubesandbox_swe_e2e import (
    STDERR_BEGIN,
    STDERR_END,
    STDOUT_BEGIN,
    STDOUT_END,
    extract_failure_details,
    parse_affinetes_output,
)
from cubesandbox_swe.legacy import run_affinetes_cubesandbox_swe_e2e as verifier


def test_extract_failure_details_from_rspec_json() -> None:
    payload = {
        "examples": [
            {"status": "passed", "full_description": "passes"},
            {
                "status": "failed",
                "full_description": "RuboCop example fails",
                "file_path": "./spec/example_spec.rb",
                "line_number": 12,
                "exception": {
                    "message": "expected offense text",
                    "backtrace": ["./spec/example_spec.rb:12", "./lib/example.rb:1"],
                },
            },
        ]
    }

    assert extract_failure_details(json.dumps(payload)) == [
        {
            "full_description": "RuboCop example fails",
            "file_path": "./spec/example_spec.rb",
            "line_number": "12",
            "message": "expected offense text",
            "backtrace": "./spec/example_spec.rb:12\n./lib/example.rb:1",
        }
    ]


def test_extract_failure_details_ignores_non_json() -> None:
    assert extract_failure_details("not json") == []


def test_parse_output_omits_failure_details_when_required_tests_pass(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "canary",
        types.SimpleNamespace(verify_canary=lambda canaries, passed, failed: (False, "")),
    )
    monkeypatch.setitem(
        sys.modules,
        "utils",
        types.SimpleNamespace(
            parse_test_output=lambda stdout, stderr, language, command: (
                {"required test passes"},
                {"canary test fails"},
            )
        ),
    )

    test_stdout = json.dumps(
        {
            "examples": [
                {
                    "status": "failed",
                    "full_description": "canary test fails",
                    "file_path": "./spec/canary_spec.rb",
                    "line_number": 3,
                    "exception": {"message": "expected randomized canary mismatch"},
                }
            ]
        }
    )
    stdout = (
        f"{STDOUT_BEGIN}\n{test_stdout}\n{STDOUT_END}\n"
        f"{STDERR_BEGIN}\n\n{STDERR_END}\n"
    )

    result = parse_affinetes_output(
        {
            "repo_language": "ruby",
            "test_command": "bundle exec rspec",
            "fail_to_pass": ["required test passes"],
            "pass_to_pass": [],
        },
        stdout,
        "",
        {"canaries": [{"name": "canary test fails"}]},
    )

    assert result["score"] == 1.0
    assert result["test_stats"]["all_passed"] is True
    assert "failure_details" not in result["test_stats"]


def test_run_task_uses_upstream_pause_connect_without_host_mount(monkeypatch, tmp_path) -> None:
    created_kwargs = {}
    connected = []
    uploaded = []

    class FakeSandbox:
        def __init__(self, sandbox_id: str, state: str = "running") -> None:
            self.sandbox_id = sandbox_id
            self.template_id = "template-id"
            self.state = state

        @classmethod
        def create(cls, **kwargs):
            created_kwargs.update(kwargs)
            return cls("sandbox-id")

        @classmethod
        def connect(cls, sandbox_id):
            connected.append(sandbox_id)
            return cls(sandbox_id, state="running")

        def pause(self, **kwargs):
            self.state = "paused"

        def get_info(self):
            return {"state": self.state}

        def kill(self):
            self.state = "killed"

    class FakeExecutor:
        def __init__(self, sandbox_id, **kwargs):
            self.sandbox_id = sandbox_id

        def run(self, command, **kwargs):
            return {"exit_code": 0, "stdout": "", "stderr": ""}

    task = {
        "instance_id": "example__repo-1",
        "dockerhub_tag": "image:tag",
        "repo_language": "python",
        "test_command": "pytest",
        "fail_to_pass": ["required test"],
        "pass_to_pass": [],
    }
    task_path = tmp_path / "task.json"
    patch_path = tmp_path / "fix.diff"
    task_path.write_text(json.dumps(task), encoding="utf-8")
    patch_path.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")

    monkeypatch.setattr(verifier, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(verifier, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(verifier, "CubeSandboxExecutor", FakeExecutor)
    monkeypatch.setattr(verifier, "upload_verifier_assets", lambda executor, script: uploaded.append(script))
    monkeypatch.setattr(
        verifier,
        "run_verifier_script",
        lambda executor, timeout: {
            "exit_code": 0,
            "stdout": f"{STDOUT_BEGIN}\nok\n{STDOUT_END}\n{STDERR_BEGIN}\n\n{STDERR_END}\n",
            "stderr": "",
            "affinetes_exit_code": "0",
        },
    )
    monkeypatch.setitem(sys.modules, "cubesandbox", types.SimpleNamespace(Sandbox=FakeSandbox))
    monkeypatch.setitem(
        sys.modules,
        "canary",
        types.SimpleNamespace(
            generate_canary=lambda language, command, test_patch, augmented_patch: None,
            verify_canary=lambda canaries, passed, failed: (False, ""),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "utils",
        types.SimpleNamespace(
            NETWORK_BLOCKLIST_SCRIPT=":",
            parse_test_output=lambda stdout, stderr, language, command: ({"required test"}, set()),
        ),
    )

    result = verifier.run_task(
        types.SimpleNamespace(
            task_json=str(task_path),
            fix_patch=str(patch_path),
            template_id="template-id",
            timeout=300,
            wait_timeout=30,
            verify_timeout=60,
        )
    )

    assert result["status"] == "ok"
    assert created_kwargs == {"template": "template-id", "timeout": 300}
    assert connected == ["sandbox-id"]
    assert result["state_after_save"] == "paused"
    assert result["state_after_restore"] == "running"
    assert result["transport"] == "cubecli-exec:/workspace/cubesandbox-swe"
    old_mount = "/" + "mnt/swe"
    assert uploaded and old_mount not in uploaded[0]
