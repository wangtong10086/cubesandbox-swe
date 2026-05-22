from __future__ import annotations

import subprocess

import pytest

from cubesandbox_swe import cubesandbox_mcp as mcp


def test_normalize_app_path_stays_under_app() -> None:
    assert mcp.normalize_app_path("lib/example.rb") == "/app/lib/example.rb"
    assert mcp.normalize_app_path("/app/lib/../README.md") == "/app/README.md"


def test_normalize_app_path_rejects_escape() -> None:
    with pytest.raises(ValueError, match="must stay under /app"):
        mcp.normalize_app_path("../../etc/passwd")


def test_normalize_absolute_path_rejects_relative() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        mcp.normalize_absolute_path("workspace/file")


def test_executor_run_uses_cubecli_exec(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = b"^@ok\r\r\n"
        stderr = b""

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Completed()

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli", workdir="/app", default_timeout=99)

    result = executor.run("pwd")

    assert result["exit_code"] == 0
    assert result["stdout"] == "ok\n"
    assert calls[0][0][:3] == ["script", "-q", "-c"]
    assert "sudo /tmp/cubecli exec -i -t sandbox-id /bin/bash -lc" in calls[0][0][3]
    assert "cd /app && pwd" in calls[0][0][3]
    assert calls[0][1]["timeout"] == 99


def test_apply_patch_does_not_put_patch_in_argv(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Completed()

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")
    patch = "diff --git a/a.py b/a.py\n+secret-ish content\n"

    result = executor.apply_patch(patch)

    assert result["exit_code"] == 0
    assert patch not in calls[0][3]
    assert "base64 -d" in calls[0][3]


def test_executor_run_reports_timeout(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli", default_timeout=1)

    with pytest.raises(subprocess.TimeoutExpired):
        executor.run("sleep 10")


def test_executor_run_parses_cubecli_exit_from_pty(monkeypatch) -> None:
    class Completed:
        returncode = 0
        stdout = b"^@before\r\r\ncubecli run fail: exec failed with exit code 7\n"
        stderr = b""

    monkeypatch.setattr(mcp.subprocess, "run", lambda *args, **kwargs: Completed())
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")

    result = executor.run("printf before; exit 7")

    assert result["exit_code"] == 7
    assert result["stdout"] == "before"
    assert result["stderr"] == "cubecli exec failed with exit code 7\n"


def test_diff_disables_git_pager(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Completed()

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")

    executor.diff("'*.rb'")

    assert "git --no-pager diff --no-ext-diff --no-color --cached" in calls[0][3]


def test_write_text_file_chunks_through_base64(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Completed()

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")

    executor.write_text_file("/workspace/cubesandbox-swe/full_script.sh", "hello", mode=0o755)

    joined = "\n".join(call[3] for call in calls)
    assert "mkdir -p -- /workspace/cubesandbox-swe" in joined
    assert "base64 -d >> /workspace/cubesandbox-swe/full_script.sh" in joined
    assert "chmod 755 -- /workspace/cubesandbox-swe/full_script.sh" in joined


def test_read_text_file_can_return_untruncated_output(monkeypatch) -> None:
    responses = [
        b"__CUBESANDBOX_SIZE_BEGIN__7__CUBESANDBOX_SIZE_END__",
        b"__CUBESANDBOX_B64_BEGIN__Y29udGVudA==__CUBESANDBOX_B64_END__",
    ]

    class Completed:
        returncode = 0
        stderr = b""

        @property
        def stdout(self):
            return responses.pop(0)

    monkeypatch.setattr(mcp.subprocess, "run", lambda *args, **kwargs: Completed())
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")

    assert executor.read_text_file("/workspace/cubesandbox-swe/stdout.log") == "content"
