from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from cubesandbox_swe.legacy import collect_cubesandbox_codex_swe50 as collect


def test_collect_run_one_passes_model_preflight_flags(monkeypatch, tmp_path) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Completed()

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    args = SimpleNamespace(
        model="Qwen/Qwen3.6-27B",
        reasoning_effort="medium",
        wire_api="responses",
        solve_timeout=480,
        verify_timeout=900,
        model_preflight_timeout=180,
        skip_model_preflight=True,
        output_root=tmp_path,
        codex_http_proxy="",
        rollout_miner_hotkey="local",
        rollout_validator_hotkey="validator",
        run_timeout=1000,
        max_solve_attempts=2,
        max_verify_attempts=2,
        force=False,
    )
    job = {
        "job_key": "11827:rep0",
        "task_id": 11827,
        "instance_id": "sparfenyuk__mcp-proxy-2",
        "image": "image",
        "template_id": "template",
        "task_json": "task.json",
        "rep": 0,
        "run_id": "run",
        "run_dir": str(tmp_path / "runs" / "11827" / "rep_0"),
    }

    collect.run_one(args, job, tmp_path / "manifest.json")

    cmd = calls[0][0]
    assert "--model-preflight-timeout" in cmd
    assert cmd[cmd.index("--model-preflight-timeout") + 1] == "180"
    assert "--max-solve-attempts" in cmd
    assert cmd[cmd.index("--max-solve-attempts") + 1] == "2"
    assert "--max-verify-attempts" in cmd
    assert cmd[cmd.index("--max-verify-attempts") + 1] == "2"
    assert "--skip-model-preflight" in cmd


def test_collect_run_one_reruns_incomplete_started_result(monkeypatch, tmp_path) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Completed()

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    args = SimpleNamespace(
        model="Qwen/Qwen3.6-27B",
        reasoning_effort="medium",
        wire_api="responses",
        solve_timeout=480,
        verify_timeout=900,
        model_preflight_timeout=180,
        skip_model_preflight=True,
        output_root=tmp_path,
        codex_http_proxy="",
        rollout_miner_hotkey="local",
        rollout_validator_hotkey="validator",
        run_timeout=1000,
        max_solve_attempts=2,
        max_verify_attempts=2,
        force=False,
    )
    run_dir = tmp_path / "runs" / "11827" / "rep_0"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(json.dumps({"status": "started"}), encoding="utf-8")
    job = {
        "job_key": "11827:rep0",
        "task_id": 11827,
        "instance_id": "sparfenyuk__mcp-proxy-2",
        "image": "image",
        "template_id": "template",
        "task_json": "task.json",
        "rep": 0,
        "run_id": "run",
        "run_dir": str(run_dir),
    }

    result = collect.run_one(args, job, tmp_path / "manifest.json")

    assert calls
    assert result["status"] == "done"


def test_build_jobs_from_seed_manifest_selects_copy_and_rerun(tmp_path) -> None:
    seed_manifest = tmp_path / "old" / "manifest.json"
    seed_manifest.parent.mkdir()
    seed_manifest.write_text(
        collect.json.dumps(
            {
                "runs": {
                    "1:rep0": {
                        "job_key": "1:rep0",
                        "task_id": 1,
                        "rep": 0,
                        "instance_id": "repo__issue-1",
                        "image": "image-a",
                        "template_id": "template-a",
                        "task_json": "task-a.json",
                        "run_id": "old-ok",
                        "run_dir": str(tmp_path / "old" / "runs" / "1" / "rep_0"),
                        "result_status": "ok",
                    },
                    "2:rep1": {
                        "job_key": "2:rep1",
                        "task_id": 2,
                        "rep": 1,
                        "instance_id": "repo__issue-2",
                        "image": "image-b",
                        "template_id": "template-b",
                        "task_json": "task-b.json",
                        "run_id": "old-error",
                        "run_dir": str(tmp_path / "old" / "runs" / "2" / "rep_1"),
                        "result_status": "error",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        seed_manifest=seed_manifest,
        rerun_result_status="error,no_patch,failed",
        copy_result_status="ok",
        output_root=tmp_path / "new",
    )

    jobs = collect.build_jobs_from_seed_manifest(args)

    assert [job["source_run_policy"] for job in jobs] == ["copied_success", "rerun_failure"]
    assert jobs[0]["run_id"] == "old-ok"
    assert jobs[1]["run_id"] != "old-error"
    assert jobs[1]["run_dir"] == str(tmp_path / "new" / "runs" / "2" / "rep_1")


def test_build_jobs_from_seed_manifest_labels_copied_model_errors(tmp_path: Path) -> None:
    seed_manifest = tmp_path / "manifest.json"
    seed_manifest.write_text(
        json.dumps(
            {
                "runs": {
                    "1:rep0": {
                        "job_key": "1:rep0",
                        "task_id": 1,
                        "rep": 0,
                        "instance_id": "repo__issue-1",
                        "image": "image-a",
                        "template_id": "template-a",
                        "task_json": "task-a.json",
                        "run_id": "old-no-patch",
                        "run_dir": str(tmp_path / "old" / "runs" / "1" / "rep_0"),
                        "result_status": "no_patch",
                    },
                    "2:rep0": {
                        "job_key": "2:rep0",
                        "task_id": 2,
                        "rep": 0,
                        "instance_id": "repo__issue-2",
                        "image": "image-b",
                        "template_id": "template-b",
                        "task_json": "task-b.json",
                        "run_id": "old-failed",
                        "run_dir": str(tmp_path / "old" / "runs" / "2" / "rep_0"),
                        "result_status": "failed",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        seed_manifest=seed_manifest,
        rerun_result_status="error",
        copy_result_status="ok,no_patch,failed",
        output_root=tmp_path / "new",
    )

    jobs = collect.build_jobs_from_seed_manifest(args)

    assert [job["source_run_policy"] for job in jobs] == ["copied_model_error", "copied_model_error"]
