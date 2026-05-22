#!/usr/bin/env python3
"""Run SWE-INFINITE with Codex solving inside CubeSandbox, then verify on CubeSandbox."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from cubesandbox_swe.codex_agent import build_swe_prompt, parse_codex_json_output
from cubesandbox_swe.cubesandbox_mcp import DEFAULT_CUBECLI, CubeSandboxExecutor


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"
DEFAULT_RESULTS_DIR = BASE_DIR / "results"
DEFAULT_RUNS_DIR = BASE_DIR / "swe-e2e-runs"
RESULTS_DIR = DEFAULT_RESULTS_DIR
RUNS_DIR = DEFAULT_RUNS_DIR
SDK_PATH = BASE_DIR / "third_party" / "CubeSandbox" / "sdk" / "python"
SWE_INFINITE_PATH = BASE_DIR / "third_party" / "affinetes" / "environments" / "SWE-INFINITE"

DEFAULT_TASK_JSON = DEFAULT_RESULTS_DIR / "swe_infinite_task_1.json"
DEFAULT_SOLVE_TEMPLATE = "swe-task1-rubocop-runner-v1"
DEFAULT_VERIFY_TEMPLATE = "swe-task1-rubocop-runner-v1"
CUBECLI = DEFAULT_CUBECLI
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")
DEFAULT_DIFF_FILTER = (
    "'*.js' '*.ts' '*.jsx' '*.tsx' '*.py' '*.java' '*.go' "
    "'*.c' '*.cpp' '*.h' '*.rs' '*.rb' '*.php' '*.cs' "
    "'*.swift' '*.kt' '*.scala' '*.vue' '*.svelte' "
    "'*.yaml' '*.yml' '*.toml' '*.json'"
)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def resolve_runtime_args(args: argparse.Namespace) -> None:
    args.model = args.model or os.environ.get("OPENAI_MODEL", "gpt-5.5")
    args.wire_api = args.wire_api or os.environ.get("SWE_INFINITE_CODEX_WIRE_API", "responses")
    args.reasoning_effort = args.reasoning_effort or os.environ.get("SWE_INFINITE_CODEX_REASONING_EFFORT", "medium")
    if args.codex_http_proxy is None:
        args.codex_http_proxy = (
            os.environ.get("SWE_INFINITE_CODEX_HTTP_PROXY")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("http_proxy")
            or ""
        )


def configure_environment(args: argparse.Namespace) -> None:
    global RESULTS_DIR, RUNS_DIR

    RESULTS_DIR = Path(args.output_dir).resolve()
    RUNS_DIR = Path(args.runs_dir).resolve()
    load_dotenv(ENV_PATH)
    resolve_runtime_args(args)
    sys.path.insert(0, str(SDK_PATH))
    sys.path.insert(0, str(SWE_INFINITE_PATH))

    for key in PROXY_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
    os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
    os.environ.setdefault("CUBE_API_URL", "http://127.0.0.1:3000")
    os.environ.setdefault("CUBE_PROXY_NODE_IP", "127.0.0.1")
    os.environ["CUBESANDBOX_SDK_PATH"] = str(SDK_PATH)
    os.environ["SWE_INFINITE_CUBE_TEMPLATE_ID"] = args.solve_template
    os.environ["SWE_INFINITE_CUBE_RUNS_DIR"] = str(RUNS_DIR)
    os.environ.setdefault("SWE_INFINITE_CODEX_WIRE_API", args.wire_api)
    os.environ.setdefault("SWE_INFINITE_CODEX_REASONING_EFFORT", args.reasoning_effort)
    if args.codex_http_proxy:
        os.environ["SWE_INFINITE_CODEX_HTTP_PROXY"] = args.codex_http_proxy


def codex_process_env(args: argparse.Namespace, api_key: str, codex_home: Path) -> dict[str, str]:
    env = {
        "CODEX_HOME": str(codex_home),
        "CODEX_API_KEY": api_key,
        "HOME": str(codex_home),
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", os.environ.get("LANG", "C.UTF-8")),
    }
    if args.codex_http_proxy:
        env.update(
            {
                "HTTP_PROXY": args.codex_http_proxy,
                "HTTPS_PROXY": args.codex_http_proxy,
                "http_proxy": args.codex_http_proxy,
                "https_proxy": args.codex_http_proxy,
                "NO_PROXY": os.environ.get("NO_PROXY", "localhost,127.0.0.1,::1"),
                "no_proxy": os.environ.get("no_proxy", "localhost,127.0.0.1,::1"),
            }
        )
    return env


def model_credentials() -> tuple[str, str]:
    api_base = os.environ.get("OPENAI_BASE_URL") or os.environ.get("CHUTES_BASE_URL") or ""
    if not api_base:
        raise RuntimeError(".env must set OPENAI_BASE_URL or CHUTES_BASE_URL")
    if endpoint_uses_local_no_auth(api_base):
        return api_base.rstrip("/"), "no-auth"

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("CHUTES_API_KEY")
    if not api_key:
        raise RuntimeError(".env must set OPENAI_API_KEY or CHUTES_API_KEY")
    return api_base.rstrip("/"), api_key


def endpoint_uses_local_no_auth(api_base: str) -> bool:
    parsed = urlparse(api_base)
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def cubesandbox_mcp_command_args(args: argparse.Namespace, sandbox_id: str) -> list[str]:
    return [
        "-i",
        f"PATH={os.environ.get('PATH', os.defpath)}",
        f"PYTHONPATH={BASE_DIR}",
        "PYTHONUNBUFFERED=1",
        f"CUBESANDBOX_SANDBOX_ID={sandbox_id}",
        f"CUBESANDBOX_CUBECLI={CUBECLI}",
        "CUBESANDBOX_WORKDIR=/app",
        f"CUBESANDBOX_COMMAND_TIMEOUT={max(args.solve_timeout, 120)}",
        f"CUBESANDBOX_DIFF_FILTER={DEFAULT_DIFF_FILTER}",
        sys.executable,
        "-m",
        "cubesandbox_swe.cubesandbox_mcp",
    ]


def build_codex_config(args: argparse.Namespace, api_base: str, *, sandbox_id: str | None = None) -> str:
    config = (
        f"model = {json.dumps(args.model)}\n"
        'model_provider = "chutes"\n\n'
        "[model_providers.chutes]\n"
        'name = "Chutes"\n'
        'env_key = "CODEX_API_KEY"\n'
        f"base_url = {json.dumps(api_base.rstrip('/'))}\n"
        f"wire_api = {json.dumps(args.wire_api)}\n\n"
        f"model_reasoning_effort = {json.dumps(args.reasoning_effort)}\n"
    )
    if sandbox_id:
        config += (
            "\n[mcp_servers.cubesandbox]\n"
            'command = "/usr/bin/env"\n'
            f"args = {json.dumps(cubesandbox_mcp_command_args(args, sandbox_id))}\n"
            "required = true\n"
            "supports_parallel_tool_calls = false\n"
            'default_tools_approval_mode = "approve"\n'
            "startup_timeout_sec = 20\n"
            f"tool_timeout_sec = {max(args.solve_timeout, 120)}\n"
            'enabled_tools = ["cube_run", "cube_read_file", "cube_apply_patch", "cube_diff"]\n'
            "\n[mcp_servers.cubesandbox.tools.cube_run]\n"
            'approval_mode = "approve"\n'
            "\n[mcp_servers.cubesandbox.tools.cube_read_file]\n"
            'approval_mode = "approve"\n'
            "\n[mcp_servers.cubesandbox.tools.cube_apply_patch]\n"
            'approval_mode = "approve"\n'
            "\n[mcp_servers.cubesandbox.tools.cube_diff]\n"
            'approval_mode = "approve"\n'
        )
    return config


def write_codex_config(
    codex_home: Path,
    args: argparse.Namespace,
    api_base: str,
    *,
    sandbox_id: str | None = None,
) -> Path:
    codex_home.mkdir(parents=True, exist_ok=True)
    config_path = codex_home / "config.toml"
    config_path.write_text(build_codex_config(args, api_base, sandbox_id=sandbox_id), encoding="utf-8")
    return config_path


def redact_env_secrets(text: str) -> str:
    redacted = text
    for key in ("OPENAI_API_KEY", "CHUTES_API_KEY", "CODEX_API_KEY"):
        value = os.environ.get(key)
        if value:
            redacted = redacted.replace(value, f"<redacted:{key}>")
    return redacted


def last_codex_json_error(stdout: str) -> str:
    last_error = ""
    for raw_line in stdout.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "error" and isinstance(event.get("message"), str):
            last_error = event["message"]
        elif event.get("type") == "turn.failed":
            error = event.get("error") or {}
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                last_error = error["message"]
    return last_error


def recover_cube_diff_patch(conversation: list[dict[str, Any]]) -> str:
    for item in reversed(conversation):
        if item.get("type") != "mcp_tool_call" or item.get("tool") != "cube_diff":
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        for content in result.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "text":
                continue
            try:
                payload = json.loads(str(content.get("text") or ""))
            except json.JSONDecodeError:
                continue
            stdout = str(payload.get("stdout") or "").lstrip()
            if stdout.strip():
                return stdout.rstrip("\n") + "\n"
    return ""


def stringify_process_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def preflight_codex_model(args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_model_preflight:
        return {"status": "skipped"}

    api_base, api_key = model_credentials()
    codex_path = shutil.which("codex")
    if not codex_path:
        raise RuntimeError("codex CLI was not found on PATH")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-preflight-", dir=RUNS_DIR, ignore_cleanup_errors=True) as tmp_dir:
        codex_home = Path(tmp_dir)
        control_dir = codex_home / "control"
        control_dir.mkdir()
        write_codex_config(codex_home, args, api_base)
        env = codex_process_env(args, api_key, codex_home)
        try:
            proc = subprocess.run(
                [
                    codex_path,
                    "--ask-for-approval",
                    "never",
                    "exec",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--ignore-rules",
                    "--json",
                    "-",
                ],
                cwd=control_dir,
                input="Reply with exactly: OK",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=args.model_preflight_timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = stringify_process_output(exc.stdout)
            stderr = stringify_process_output(exc.stderr)
            detail = last_codex_json_error(stdout) or stderr
            detail = redact_env_secrets(detail).strip().replace("\n", " ")
            suffix = f": {detail[:800]}" if detail else ""
            raise RuntimeError(f"codex model preflight timed out after {args.model_preflight_timeout}s{suffix}") from exc

    if proc.returncode != 0:
        detail = last_codex_json_error(proc.stdout) or proc.stderr or proc.stdout
        detail = redact_env_secrets(detail).strip().replace("\n", " ")
        raise RuntimeError(f"codex model preflight failed with exit {proc.returncode}: {detail[:800]}")

    return {"status": "ok", "model": args.model, "wire_api": args.wire_api}


def ensure_runtime_allowed(args: argparse.Namespace) -> None:
    if args.codex_location != "sandbox":
        raise RuntimeError("only the CubeSandbox MCP runtime is supported; use --codex-location sandbox")


def build_cubesandbox_runtime_prompt(base_prompt: str) -> str:
    return (
        "You are solving this task inside a CubeSandbox runtime.\n"
        "The task repository is available only inside the CubeSandbox sandbox at /app.\n"
        "Do not inspect or modify the host filesystem. Do not use local shell commands for task work.\n"
        "Use only these MCP tools for repository operations:\n"
        "- cube_run: run shell commands in /app inside CubeSandbox.\n"
        "- cube_read_file: read files from /app inside CubeSandbox.\n"
        "- cube_apply_patch: apply a git patch to /app inside CubeSandbox.\n"
        "- cube_diff: inspect the current source diff in /app inside CubeSandbox.\n"
        "This is a single-turn execution: do not end with a plan, status update, or note that you will inspect more.\n"
        "If you need more information, call another CubeSandbox tool in the same response.\n"
        "Avoid reading huge files in full; use targeted grep/sed commands for config or documentation.\n"
        "Only provide a final assistant message after you have applied a patch and inspected a non-empty cube_diff.\n"
        "When you are finished, leave the source changes in /app and respond briefly.\n\n"
        f"{base_prompt}"
    )


def prepare_cubesandbox_runtime(executor: CubeSandboxExecutor) -> None:
    from utils import NETWORK_BLOCKLIST_SCRIPT, NORMALIZE_TIMESTAMPS_SCRIPT, SANITIZE_GIT_SCRIPT

    network = executor.run(NETWORK_BLOCKLIST_SCRIPT, timeout=30)
    if network["exit_code"] != 0:
        raise RuntimeError(f"CubeSandbox network blocklist failed: {network['stderr'] or network['stdout']}")

    prepared = executor.run("test -f /etc/swe-infinite-prepared", timeout=5)
    if prepared["exit_code"] == 0:
        return

    executor.run(f"({SANITIZE_GIT_SCRIPT}) >/tmp/swe_sanitize.log 2>&1 || true", timeout=90)
    executor.run("bash -lc true >/tmp/swe_shell_warmup.log 2>&1 || true", timeout=60)
    executor.run(f"({NORMALIZE_TIMESTAMPS_SCRIPT}) >/tmp/swe_normalize.log 2>&1 || true", timeout=180)


def solve_cubesandbox_runtime(args: argparse.Namespace) -> dict:
    from cubesandbox import Sandbox

    task = json.loads(Path(args.task_json).read_text(encoding="utf-8"))
    api_base, api_key = model_credentials()
    codex_path = shutil.which("codex")
    if not codex_path:
        raise RuntimeError("codex CLI was not found on PATH")

    run_dir = RUNS_DIR / f"cube-codex-{args.run_id}"
    control_dir = run_dir / "control"
    codex_home = run_dir / "codex_home"
    run_dir.mkdir(parents=True)
    control_dir.mkdir()
    codex_home.mkdir()

    sandbox = Sandbox.create(template=args.solve_template, timeout=args.solve_timeout + 300)
    try:
        (run_dir / "sandbox_id").write_text(sandbox.sandbox_id, encoding="utf-8")
        executor = CubeSandboxExecutor(
            sandbox.sandbox_id,
            cubecli=CUBECLI,
            workdir="/app",
            default_timeout=max(args.solve_timeout, 120),
        )
        prepare_cubesandbox_runtime(executor)

        base_prompt = build_swe_prompt(
            task["problem_statement"],
            task.get("repo", ""),
            task.get("repo_language", ""),
            task.get("test_command", ""),
            task.get("fail_to_pass"),
        )
        prompt = build_cubesandbox_runtime_prompt(base_prompt)
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        write_codex_config(codex_home, args, api_base, sandbox_id=sandbox.sandbox_id)
        env = codex_process_env(args, api_key, codex_home)
        proc = subprocess.run(
            [
                codex_path,
                "--ask-for-approval",
                "never",
                "exec",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ephemeral",
                "--ignore-rules",
                "--json",
                "-",
            ],
            cwd=control_dir,
            input=prompt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=args.solve_timeout,
            env=env,
        )
        (run_dir / "codex_stdout.jsonl").write_text(proc.stdout, encoding="utf-8")
        (run_dir / "codex_stderr.log").write_text(proc.stderr, encoding="utf-8")
        total_tokens, model_calls, conversation, last_error = parse_codex_json_output(proc.stdout)
        conversation.insert(0, {"role": "user", "content": prompt})

        diff = executor.diff(DEFAULT_DIFF_FILTER)
        patch = diff["stdout"].lstrip()
        if (not patch.strip() or (patch.startswith("diff --git") and "\n@@" not in patch)) and conversation:
            patch = recover_cube_diff_patch(conversation) or patch
        if patch:
            patch = patch.rstrip("\n") + "\n"

        error = None
        if proc.returncode != 0:
            error = f"codex_error: exit {proc.returncode}: {last_error or (proc.stderr or proc.stdout)[:500]}"
        if diff["exit_code"] != 0:
            error = f"cube_diff_error: {diff['stderr'] or diff['stdout']}"
        return {
            "task": task,
            "agent_result": {
                "success": bool(patch),
                "error": error,
                "model_calls": model_calls,
                "total_tokens": total_tokens,
                "patch": patch,
                "conversation_items": len(conversation),
                "run_location": "cubesandbox-mcp",
                "run_dir": str(run_dir),
                "sandbox_id": sandbox.sandbox_id,
            },
        }
    finally:
        try:
            sandbox.kill()
        except Exception:
            pass


def verify(args: argparse.Namespace, fix_patch_path: Path) -> dict:
    from cubesandbox_swe.legacy import run_affinetes_cubesandbox_swe_e2e as verifier

    verifier.configure_imports()
    verifier.local_cube_env()
    return verifier.run_task(
        SimpleNamespace(
            task_json=str(args.task_json),
            fix_patch=str(fix_patch_path),
            template_id=args.verify_template,
            timeout=args.verify_timeout + 300,
            wait_timeout=180,
            verify_timeout=args.verify_timeout,
        )
    )


def redact_text(text: str) -> str:
    return redact_env_secrets(text)


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return redact_text(path.read_text(encoding="utf-8", errors="replace"))


def read_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in read_text_if_exists(path).splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"type": "unparsed", "line": line})
    return events


def trajectory_attempt_paths(run_dir: Path, attempt: int) -> dict[str, Path]:
    if attempt == 1:
        return {
            "prompt": run_dir / "prompt.txt",
            "stdout": run_dir / "codex_stdout.jsonl",
            "stderr": run_dir / "codex_stderr.log",
        }
    prompt = run_dir / f"feedback_{attempt}.txt"
    if not prompt.exists():
        matches = sorted(run_dir.glob(f"feedback_{attempt}*.txt"))
        if matches:
            prompt = matches[0]
    return {
        "prompt": prompt,
        "stdout": run_dir / f"codex_retry_{attempt}_stdout.jsonl",
        "stderr": run_dir / f"codex_retry_{attempt}_stderr.log",
    }


def build_trajectory(result: dict[str, Any], task: dict[str, Any] | None = None) -> dict[str, Any]:
    task = task or {}
    run_dir_value = (result.get("agent_result") or {}).get("run_dir")
    if not run_dir_value:
        for attempt in result.get("attempts", []):
            run_dir_value = (attempt.get("agent_result") or {}).get("run_dir")
            if run_dir_value:
                break
    run_dir = Path(run_dir_value) if run_dir_value else None

    trajectory: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": {
            "task_json": result.get("task_json"),
            "instance_id": result.get("instance_id") or task.get("instance_id"),
            "dockerhub_tag": result.get("dockerhub_tag") or task.get("dockerhub_tag"),
            "repo": task.get("repo"),
            "repo_language": task.get("repo_language"),
            "test_command": task.get("test_command"),
            "fail_to_pass": task.get("fail_to_pass"),
            "pass_to_pass": task.get("pass_to_pass"),
            "problem_statement": task.get("problem_statement"),
        },
        "environment": {
            "codex_location": result.get("codex_location"),
            "solve_template": result.get("solve_template"),
            "verify_template": result.get("verify_template"),
            "model": result.get("model"),
            "reasoning_effort": result.get("reasoning_effort"),
            "wire_api": result.get("wire_api"),
            "codex_run_dir": str(run_dir) if run_dir else None,
        },
        "final": {
            "status": result.get("status"),
            "fix_patch_path": result.get("fix_patch_path"),
            "fix_patch_bytes": result.get("fix_patch_bytes"),
            "verify_result_file": result.get("verify_result_file"),
            "verify": result.get("verify"),
        },
        "attempts": [],
    }

    for attempt in result.get("attempts", []):
        attempt_no = int(attempt.get("attempt", len(trajectory["attempts"]) + 1))
        paths = trajectory_attempt_paths(run_dir, attempt_no) if run_dir else {}
        verify_json: dict[str, Any] = {}
        verify_file = attempt.get("verify_result_file")
        if verify_file and Path(verify_file).exists():
            verify_json = json.loads(read_text_if_exists(Path(verify_file)))

        verify_run_dir = verify_json.get("run_dir")
        verifier_stdout = ""
        verifier_stderr = ""
        if verify_run_dir:
            verifier_stdout = read_text_if_exists(Path(verify_run_dir) / "container_stdout.log")
            verifier_stderr = read_text_if_exists(Path(verify_run_dir) / "container_stderr.log")

        patch_path = Path(attempt["fix_patch_path"]) if attempt.get("fix_patch_path") else None
        trajectory["attempts"].append({
            "attempt": attempt_no,
            "agent_result": attempt.get("agent_result"),
            "prompt_path": str(paths.get("prompt")) if paths.get("prompt") else None,
            "prompt": read_text_if_exists(paths["prompt"]) if paths.get("prompt") else "",
            "codex_stdout_path": str(paths.get("stdout")) if paths.get("stdout") else None,
            "codex_events": read_jsonl_if_exists(paths["stdout"]) if paths.get("stdout") else [],
            "codex_stderr_path": str(paths.get("stderr")) if paths.get("stderr") else None,
            "codex_stderr": read_text_if_exists(paths["stderr"]) if paths.get("stderr") else "",
            "patch_path": str(patch_path) if patch_path else None,
            "patch": read_text_if_exists(patch_path) if patch_path else "",
            "verify_result_file": verify_file,
            "verify_result": verify_json,
            "verifier_container_stdout": verifier_stdout,
            "verifier_container_stderr": verifier_stderr,
        })
    return trajectory


def parse_rollout_task_id(value: Any, task_json: str | None = None) -> int | str | None:
    if value is not None and value != "":
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return value
    if task_json:
        match = re.search(r"(\d+)(?!.*\d)", Path(task_json).stem)
        if match:
            return int(match.group(1))
    return None


def conversation_from_trajectory(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    conversation: list[dict[str, Any]] = []
    for attempt in trajectory.get("attempts", []):
        prompt = attempt.get("prompt")
        if prompt:
            conversation.append({"role": "user", "content": prompt})
        for event in attempt.get("codex_events", []):
            if event.get("type") == "item.completed" and isinstance(event.get("item"), dict):
                conversation.append(event["item"])
    return conversation


def build_rollout_bucket_record(
    result: dict[str, Any],
    task: dict[str, Any] | None = None,
    trajectory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = task or {}
    trajectory = trajectory or build_trajectory(result, task)
    verify = result.get("verify") or {}
    test_stats = verify.get("test_stats") or {}
    agent_result = result.get("agent_result") or {}
    total_tokens = int(agent_result.get("total_tokens") or 0)
    model_calls = int(agent_result.get("model_calls") or 0)
    score = float(verify.get("score") or 0.0)
    task_id = parse_rollout_task_id(
        result.get("rollout_task_id") or task.get("task_id"),
        result.get("task_json"),
    )

    return {
        "miner_hotkey": result.get("rollout_miner_hotkey") or "local-cubesandbox",
        "model_revision": result.get("rollout_model_revision") or result.get("run_id") or "",
        "model": result.get("rollout_model") or result.get("model") or "",
        "env": "SWE-INFINITE",
        "task_id": task_id,
        "score": score,
        "latency_ms": int(result.get("latency_ms") or 0),
        "timestamp": int(time.time() * 1000),
        "validator_hotkey": result.get("rollout_validator_hotkey") or "executor-SWE-INFINITE-local",
        "block_number": int(result.get("rollout_block_number") or 0),
        "signature": result.get("rollout_signature") or "",
        "extra": {
            "task_id": task_id,
            "task_type": "swe-infinite",
            "agent_type": "codex",
            "instance_id": result.get("instance_id") or task.get("instance_id"),
            "repo": task.get("repo", ""),
            "repo_language": task.get("repo_language", ""),
            "problem_statement": task.get("problem_statement", ""),
            "fix_patch": read_text_if_exists(Path(result["fix_patch_path"])) if result.get("fix_patch_path") else "",
            "conversation": conversation_from_trajectory(trajectory),
            "model_calls": model_calls,
            "model_cost": float(agent_result.get("model_cost") or 0.0),
            "total_tokens": total_tokens,
            "test_stats": test_stats,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": total_tokens,
            },
            "cubesandbox": {
                "status": result.get("status"),
                "codex_location": result.get("codex_location"),
                "solve_template": result.get("solve_template"),
                "verify_template": result.get("verify_template"),
                "reasoning_effort": result.get("reasoning_effort"),
                "wire_api": result.get("wire_api"),
                "trajectory_schema_version": trajectory.get("schema_version"),
                "attempt_count": len(result.get("attempts", [])),
                "save_restore": {
                    "state_after_save": verify.get("state_after_save"),
                    "state_after_restore": verify.get("state_after_restore"),
                },
            },
        },
    }


def write_rollout_bucket_record(result: dict[str, Any], task: dict[str, Any] | None = None) -> Path | None:
    if not result.get("attempts"):
        return None
    trajectory = build_trajectory(result, task)
    record = build_rollout_bucket_record(result, task, trajectory)
    task_id = record.get("task_id") if record.get("task_id") is not None else "unknown"
    out_path = RESULTS_DIR / f"rollout_bucket_SWE-INFINITE_{task_id}_{result.get('run_id', 'unknown')}.json"
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    latest = RESULTS_DIR / "rollout_bucket_SWE-INFINITE_latest.json"
    latest.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    result["rollout_bucket_file"] = str(out_path)
    return out_path


def write_trajectory(result: dict[str, Any], task: dict[str, Any] | None = None) -> Path | None:
    if not result.get("attempts"):
        return None
    out_path = RESULTS_DIR / f"cubesandbox_codex_trajectory_{result.get('run_id', 'unknown')}.json"
    trajectory = build_trajectory(result, task)
    out_path.write_text(json.dumps(trajectory, indent=2, ensure_ascii=False), encoding="utf-8")
    latest = RESULTS_DIR / "cubesandbox_codex_trajectory_latest.json"
    latest.write_text(json.dumps(trajectory, indent=2, ensure_ascii=False), encoding="utf-8")
    result["trajectory_file"] = str(out_path)
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-json", default=str(DEFAULT_TASK_JSON))
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--wire-api", default=None)
    parser.add_argument("--solve-template", default=DEFAULT_SOLVE_TEMPLATE)
    parser.add_argument("--verify-template", default=DEFAULT_VERIFY_TEMPLATE)
    parser.add_argument("--solve-timeout", type=int, default=1800)
    parser.add_argument("--verify-timeout", type=int, default=1800)
    parser.add_argument("--codex-http-proxy", default=None)
    parser.add_argument("--codex-location", choices=["sandbox"], default="sandbox")
    parser.add_argument("--skip-model-preflight", action="store_true")
    parser.add_argument("--model-preflight-timeout", type=int, default=90)
    parser.add_argument("--max-verify-attempts", type=int, default=1)
    parser.add_argument("--output-dir", default=str(DEFAULT_RESULTS_DIR))
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--rollout-miner-hotkey", default=os.environ.get("AFFINE_ROLLOUT_MINER_HOTKEY", "local-cubesandbox"))
    parser.add_argument("--rollout-model-revision", default=os.environ.get("AFFINE_ROLLOUT_MODEL_REVISION", ""))
    parser.add_argument("--rollout-model", default=os.environ.get("AFFINE_ROLLOUT_MODEL", ""))
    parser.add_argument("--rollout-task-id", default=os.environ.get("AFFINE_ROLLOUT_TASK_ID", ""))
    parser.add_argument("--rollout-validator-hotkey", default=os.environ.get("AFFINE_ROLLOUT_VALIDATOR_HOTKEY", "executor-SWE-INFINITE-local"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.run_id:
        args.run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:8]}"
    configure_environment(args)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id

    result = {
        "status": "started",
        "run_id": run_id,
        "task_json": str(args.task_json),
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "wire_api": args.wire_api,
        "solve_template": args.solve_template,
        "verify_template": args.verify_template,
        "codex_http_proxy": args.codex_http_proxy,
        "codex_location": args.codex_location,
        "runtime": "cubesandbox-mcp",
        "skip_model_preflight": args.skip_model_preflight,
        "model_preflight_timeout": args.model_preflight_timeout,
        "rollout_miner_hotkey": args.rollout_miner_hotkey,
        "rollout_model_revision": args.rollout_model_revision,
        "rollout_model": args.rollout_model,
        "rollout_task_id": args.rollout_task_id,
        "rollout_validator_hotkey": args.rollout_validator_hotkey,
    }
    solved_task: dict[str, Any] | None = None
    try:
        ensure_runtime_allowed(args)
        if not args.skip_model_preflight:
            result["model_preflight"] = preflight_codex_model(args)
        solved = solve_cubesandbox_runtime(args)
        solved_task = solved["task"]
        patch = solved["agent_result"].pop("patch")
        patch_path = RESULTS_DIR / f"cubesandbox_codex_fix_patch_{run_id}_attempt1.diff"
        patch_path.write_text(patch, encoding="utf-8")
        result.update({
            "instance_id": solved["task"].get("instance_id"),
            "dockerhub_tag": solved["task"].get("dockerhub_tag"),
            "agent_result": solved["agent_result"],
            "fix_patch_path": str(patch_path),
            "fix_patch_bytes": len(patch.encode("utf-8")),
            "attempts": [],
        })
        if not patch.strip():
            result["status"] = "no_patch"
            result["attempts"].append({
                "attempt": 1,
                "fix_patch_path": str(patch_path),
                "fix_patch_bytes": 0,
                "agent_result": result["agent_result"],
                "verify_result_file": None,
                "verify": {
                    "status": "no_patch",
                    "score": 0.0,
                    "test_stats": {"failure_reason": "no_patch_generated"},
                    "state_after_save": None,
                    "state_after_restore": None,
                },
            })
            result["verify"] = result["attempts"][-1]["verify"]
            return_code = 1
        else:
            verify_result = None
            current_agent_result = result["agent_result"]
            for attempt in range(1, args.max_verify_attempts + 1):
                verify_result = verify(args, patch_path)
                attempt_summary = {
                    "attempt": attempt,
                    "fix_patch_path": str(patch_path),
                    "fix_patch_bytes": len(patch.encode("utf-8")),
                    "agent_result": current_agent_result,
                    "verify_result_file": verify_result.get("result_file"),
                    "verify": {
                        "status": verify_result.get("status"),
                        "score": verify_result.get("score"),
                        "test_stats": verify_result.get("test_stats"),
                        "state_after_save": verify_result.get("state_after_save"),
                        "state_after_restore": verify_result.get("state_after_restore"),
                    },
                }
                result["attempts"].append(attempt_summary)
                if verify_result.get("status") == "ok":
                    break
                break

            result["verify_result_file"] = verify_result.get("result_file") if verify_result else None
            result["verify"] = result["attempts"][-1]["verify"] if result["attempts"] else None
            result["status"] = "ok" if verify_result and verify_result.get("status") == "ok" else "failed"
            return_code = 0 if result["status"] == "ok" else 1
    except Exception as exc:
        import traceback

        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback_tail"] = traceback.format_exc().splitlines()[-10:]
        return_code = 1
    finally:
        if result.get("attempts"):
            try:
                write_trajectory(result, solved_task)
                write_rollout_bucket_record(result, solved_task)
            except Exception as exc:
                result["trajectory_error"] = f"{type(exc).__name__}: {exc}"
        out_path = RESULTS_DIR / f"cubesandbox_codex_swe_e2e_{run_id}.json"
        latest = RESULTS_DIR / "cubesandbox_codex_swe_e2e_latest.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        latest.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({
            "status": result.get("status"),
            "instance_id": result.get("instance_id"),
            "model": result.get("model"),
            "model_preflight": result.get("model_preflight"),
            "error": result.get("error"),
            "fix_patch_bytes": result.get("fix_patch_bytes"),
            "agent_result": result.get("agent_result"),
            "verify": result.get("verify"),
            "result_file": str(out_path),
        }, indent=2, ensure_ascii=False))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
