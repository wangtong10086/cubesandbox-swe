from __future__ import annotations

from types import SimpleNamespace

import pytest

from cubesandbox_swe.legacy import run_cubesandbox_codex_swe_e2e as e2e


def make_args(**overrides):
    values = {
        "model": "test-model",
        "wire_api": "responses",
        "reasoning_effort": "low",
        "codex_http_proxy": "",
        "skip_model_preflight": False,
        "model_preflight_timeout": 10,
        "solve_timeout": 900,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_resolve_runtime_args_uses_env_defaults(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    monkeypatch.setenv("SWE_INFINITE_CODEX_WIRE_API", "chat")
    monkeypatch.setenv("SWE_INFINITE_CODEX_REASONING_EFFORT", "high")
    monkeypatch.setenv("SWE_INFINITE_CODEX_HTTP_PROXY", "http://127.0.0.1:8080")
    args = make_args(model=None, wire_api=None, reasoning_effort=None, codex_http_proxy=None)

    e2e.resolve_runtime_args(args)

    assert args.model == "env-model"
    assert args.wire_api == "chat"
    assert args.reasoning_effort == "high"
    assert args.codex_http_proxy == "http://127.0.0.1:8080"


def test_resolve_runtime_args_infers_host_proxy(monkeypatch) -> None:
    monkeypatch.delenv("SWE_INFINITE_CODEX_HTTP_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:11081")
    args = make_args(codex_http_proxy=None)

    e2e.resolve_runtime_args(args)

    assert args.codex_http_proxy == "http://127.0.0.1:11081"


def test_codex_process_env_is_minimal_and_restores_proxy(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    env = e2e.codex_process_env(make_args(codex_http_proxy="http://127.0.0.1:11081"), "secret", tmp_path)

    assert env["CODEX_HOME"] == str(tmp_path)
    assert env["CODEX_API_KEY"] == "secret"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:11081"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:11081"
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["HOME"] == str(tmp_path)
    assert "OPENAI_API_KEY" not in env


def test_build_codex_config() -> None:
    args = make_args(model="gpt-test", wire_api="responses", reasoning_effort="medium")

    config = e2e.build_codex_config(args, "https://example.test/v1/")

    assert 'model = "gpt-test"' in config
    assert 'base_url = "https://example.test/v1"' in config
    assert 'wire_api = "responses"' in config
    assert 'model_reasoning_effort = "medium"' in config


def test_build_codex_config_with_cubesandbox_mcp_does_not_embed_api_key() -> None:
    args = make_args(model="gpt-test", wire_api="responses", reasoning_effort="medium")

    config = e2e.build_codex_config(args, "https://example.test/v1/", sandbox_id="sandbox-123")

    assert "[mcp_servers.cubesandbox]" in config
    assert 'command = "/usr/bin/env"' in config
    assert 'default_tools_approval_mode = "approve"' in config
    assert "supports_parallel_tool_calls = false" in config
    assert "[mcp_servers.cubesandbox.tools.cube_apply_patch]" in config
    assert "CUBESANDBOX_SANDBOX_ID=sandbox-123" in config
    assert "codex_api_key" not in config
    assert "cubesandbox_swe.cubesandbox_mcp" in config


def test_model_credentials_uses_no_auth_for_local_proxy(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:18088/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")

    assert e2e.model_credentials() == ("http://127.0.0.1:18088/v1", "no-auth")


def test_preflight_codex_model_runs_codex(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        config_path = kwargs["env"]["CODEX_HOME"] + "/config.toml"
        assert "test-model" in e2e.Path(config_path).read_text(encoding="utf-8")
        assert kwargs["env"]["CODEX_API_KEY"] == "secret-key"
        return Completed()

    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setattr(e2e.shutil, "which", lambda name: "/usr/local/bin/codex" if name == "codex" else None)
    monkeypatch.setattr(e2e.subprocess, "run", fake_run)

    result = e2e.preflight_codex_model(make_args())

    assert result == {"status": "ok", "model": "test-model", "wire_api": "responses"}
    forbidden_flag = "--dangerously-" + "bypass-approvals-and-sandbox"
    assert forbidden_flag not in calls[0][0]
    assert calls[0][0][:4] == ["/usr/local/bin/codex", "--ask-for-approval", "never", "exec"]
    assert calls[0][0][4:6] == ["--sandbox", "read-only"]
    assert "--skip-git-repo-check" in calls[0][0]


def test_preflight_codex_model_reports_json_error(monkeypatch) -> None:
    class Completed:
        returncode = 1
        stdout = '{"type":"turn.failed","error":{"message":"stream disconnected before completion"}}\n'
        stderr = ""

    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setattr(e2e.shutil, "which", lambda name: "/usr/local/bin/codex" if name == "codex" else None)
    monkeypatch.setattr(e2e.subprocess, "run", lambda *args, **kwargs: Completed())

    with pytest.raises(RuntimeError, match="stream disconnected before completion"):
        e2e.preflight_codex_model(make_args())


def test_preflight_codex_model_reports_timeout(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise e2e.subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=kwargs["timeout"],
            output=b'{"type":"error","message":"waiting for model"}\n',
        )

    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setattr(e2e.shutil, "which", lambda name: "/usr/local/bin/codex" if name == "codex" else None)
    monkeypatch.setattr(e2e.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="timed out after 10s"):
        e2e.preflight_codex_model(make_args())


def test_runtime_rejects_non_sandbox() -> None:
    with pytest.raises(RuntimeError, match="only the CubeSandbox MCP runtime"):
        e2e.ensure_runtime_allowed(make_args(codex_location="container"))
