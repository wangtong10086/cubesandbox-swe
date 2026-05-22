from __future__ import annotations

from cubesandbox_swe.cli import main
from cubesandbox_swe.legacy import smoke_cubesandbox_swe_templates as smoke


def test_root_help(capsys) -> None:
    assert main(["--help"]) == 0
    assert "cubesandbox-swe solve" in capsys.readouterr().out


def test_template_prepare_dry_run(capsys) -> None:
    assert main(["templates", "prepare", "--dry-run"]) == 0
    assert "dry-run" in capsys.readouterr().out


def test_template_smoke_dry_run_with_options(capsys) -> None:
    assert main(["templates", "smoke", "--limit", "1", "--exec-backend", "cubecli", "--dry-run"]) == 0
    assert "smoke_cubesandbox_swe_templates" in capsys.readouterr().out


def test_template_smoke_cubecli_exec(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Completed()

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)
    result = smoke.run_cubecli_command("sandbox-id", "pwd", 120, "/tmp/cubecli")

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert calls[0][0] == [
        "sudo",
        "/tmp/cubecli",
        "exec",
        "sandbox-id",
        "/bin/bash",
        "-lc",
        "pwd",
    ]


def test_solve_dry_run(capsys) -> None:
    assert main(["solve", "--dry-run"]) == 0
    assert "run_cubesandbox_codex_swe_e2e" in capsys.readouterr().out


def test_collect_dry_run_is_cli_level(capsys) -> None:
    assert main(["collect", "swe50", "--dry-run"]) == 0
    assert "collect_cubesandbox_codex_swe50" in capsys.readouterr().out


def test_artifacts_help(capsys) -> None:
    assert main(["artifacts", "--help"]) == 0
    assert "artifacts summarize" in capsys.readouterr().out


def test_doctor_help(capsys) -> None:
    assert main(["doctor", "--help"]) == 0
    assert "--runtime-smoke" in capsys.readouterr().out
