from __future__ import annotations

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
    assert "--skip-model-preflight" in cmd
