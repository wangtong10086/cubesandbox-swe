#!/usr/bin/env python3
"""Run a SWE-INFINITE task on CubeSandbox using the affinetes verifier scaffold.

This keeps the in-sandbox verification flow aligned with
``environments/SWE-INFINITE/env.py``:

    test_patch -> augmented_test_patch -> fix_patch -> canary -> git seal -> test_command

CubeSandbox is only used as the execution backend. The script still injects the
same ``/workspace/entryscript.sh`` and parses the same output markers that the
online Docker verifier uses.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cubesandbox_swe.cubesandbox_lifecycle import restore_sandbox_state, save_sandbox_state
from cubesandbox_swe.cubesandbox_mcp import DEFAULT_CUBECLI, CubeSandboxExecutor


BASE_DIR = Path(__file__).resolve().parents[2]
RESULTS_DIR = BASE_DIR / "results"
RUNS_DIR = BASE_DIR / "swe-e2e-runs"
SDK_PATH = BASE_DIR / "third_party" / "CubeSandbox" / "sdk" / "python"
SWE_INFINITE_PATH = BASE_DIR / "third_party" / "affinetes" / "environments" / "SWE-INFINITE"

DEFAULT_TASK_JSON = RESULTS_DIR / "swe_infinite_task_1.json"
DEFAULT_FIX_PATCH = RESULTS_DIR / "task1_fix_patch.diff"
DEFAULT_TEMPLATE_ID = "swe-task1-rubocop-runner-v1"
CUBECLI = DEFAULT_CUBECLI

VERIFY_TIMEOUT = 1800
SANDBOX_WORK_DIR = "/workspace/cubesandbox-swe"
STDOUT_BEGIN = "===SWE_INFINITE_STDOUT_BEGIN==="
STDOUT_END = "===SWE_INFINITE_STDOUT_END==="
STDERR_BEGIN = "===SWE_INFINITE_STDERR_BEGIN==="
STDERR_END = "===SWE_INFINITE_STDERR_END==="


def configure_imports() -> None:
    sys.path.insert(0, str(SDK_PATH))
    sys.path.insert(0, str(SWE_INFINITE_PATH))


def local_cube_env() -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
    os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
    os.environ.setdefault("CUBE_API_URL", "http://127.0.0.1:3000")
    os.environ.setdefault("CUBE_PROXY_NODE_IP", "127.0.0.1")


def ensure_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except json.JSONDecodeError:
            return []
    return []


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def sandbox_file(name: str) -> str:
    return f"{SANDBOX_WORK_DIR}/{name}"


def adjust_canary_for_runtime(language: str, canary: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep upstream affinetes canary behavior, with local RSpec command fixups."""
    if not canary or language.lower() != "ruby":
        return canary
    test_command = str(canary.get("test_command") or "")
    if "rspec" not in test_command.lower():
        return canary
    inject_cmds = str(canary.get("inject_cmds") or "")
    match = re.search(r">\s*(/app/\S+)", inject_cmds)
    if not match:
        return canary
    rel_path = match.group(1).removeprefix("/app/")
    if rel_path in test_command:
        return canary
    adjusted = dict(canary)
    adjusted["test_command"] = f'{test_command} "{rel_path}"'
    return adjusted


def build_affinetes_full_script(task: dict[str, Any], fix_patch: str) -> tuple[str, dict[str, Any] | None]:
    from canary import generate_canary
    from utils import NETWORK_BLOCKLIST_SCRIPT

    test_command = task.get("test_command", "pytest -v --tb=no")
    test_patch = task.get("test_patch", "") or ""
    augmented_test_patch = task.get("augmented_test_patch", "") or ""

    language = task.get("repo_language", "")
    canary = generate_canary(language, test_command, test_patch, augmented_test_patch)
    canary = adjust_canary_for_runtime(language, canary)
    canary_inject = canary["inject_cmds"] if canary else ""
    effective_test_command = canary["test_command"] if canary else test_command

    apply_steps: list[str] = []
    if test_patch.strip():
        apply_steps.append(
            f'git apply --recount --whitespace=fix {sandbox_file("test_patch.diff")} 2>&1 || '
            'echo "TEST_PATCH_APPLY_FAILED"'
        )
    if augmented_test_patch.strip():
        apply_steps.append(
            f'git apply --recount --whitespace=fix {sandbox_file("augmented_test.diff")} 2>&1 || '
            'echo "AUGMENTED_PATCH_APPLY_FAILED"'
        )
    apply_cmds = "\n".join(apply_steps)

    git_seal_cmd = (
        'git -c user.email=agent@swe-infinite.local '
        '-c user.name="SWE-INFINITE Agent" add -A >/dev/null 2>&1 || true; '
        'git -c user.email=agent@swe-infinite.local '
        '-c user.name="SWE-INFINITE Agent" '
        'commit -m "test setup" --quiet --allow-empty >/dev/null 2>&1 || true'
    )

    entryscript = f"""
{NETWORK_BLOCKLIST_SCRIPT}
cd /app
{apply_cmds}
git apply --recount --whitespace=fix {sandbox_file("fix_patch.diff")} 2>&1 || {{ echo "PATCH_APPLY_FAILED"; }}
{canary_inject}
{git_seal_cmd}
{effective_test_command} > {sandbox_file("stdout.log")} 2> {sandbox_file("stderr.log")} || true
echo "{STDOUT_BEGIN}"
cat {sandbox_file("stdout.log")}
echo "{STDOUT_END}"
echo "{STDERR_BEGIN}"
cat {sandbox_file("stderr.log")}
echo "{STDERR_END}"
"""

    test_patch_lines = ""
    if test_patch.strip():
        test_patch_lines = f'echo "{b64(test_patch)}" | base64 -d > {sandbox_file("test_patch.diff")}'
    augmented_lines = ""
    if augmented_test_patch.strip():
        augmented_lines = f'echo "{b64(augmented_test_patch)}" | base64 -d > {sandbox_file("augmented_test.diff")}'

    full_script = f"""#!/bin/bash
mkdir -p {SANDBOX_WORK_DIR}
{test_patch_lines}
{augmented_lines}
echo "{b64(fix_patch)}" | base64 -d > {sandbox_file("fix_patch.diff")}
echo "{b64(entryscript)}" | base64 -d > {sandbox_file("entryscript.sh")}
chmod +x {sandbox_file("entryscript.sh")}
bash {sandbox_file("entryscript.sh")}
"""
    return full_script, canary


def write_local_verifier_inputs(run_dir: Path, full_script: str) -> None:
    (run_dir / "full_script.sh").write_text(full_script, encoding="utf-8")

def upload_verifier_assets(executor: CubeSandboxExecutor, full_script: str) -> None:
    executor.write_text_file(sandbox_file("full_script.sh"), full_script, mode=0o755)


def run_verifier_script(executor: CubeSandboxExecutor, *, timeout: int) -> dict[str, str | int]:
    command = (
        f"date -u +%FT%TZ > {sandbox_file('restored_start')}; "
        f"bash {sandbox_file('full_script.sh')} > {sandbox_file('container_stdout.log')} "
        f"2> {sandbox_file('container_stderr.log')}; "
        f"rc=$?; echo \"$rc\" > {sandbox_file('affinetes_exit_code')}; "
        f"date -u +%FT%TZ > {sandbox_file('affinetes_done')}; exit \"$rc\""
    )
    result = executor.run(command, timeout=timeout, max_bytes=None)
    return {
        "exit_code": result["exit_code"],
        "stdout": executor.read_text_file(sandbox_file("container_stdout.log")),
        "stderr": executor.read_text_file(sandbox_file("container_stderr.log")),
        "affinetes_exit_code": executor.read_text_file(sandbox_file("affinetes_exit_code")).strip(),
    }


def parse_affinetes_output(
    task: dict[str, Any],
    stdout: str,
    stderr: str,
    canary: dict[str, Any] | None,
) -> dict[str, Any]:
    from canary import verify_canary
    from utils import parse_test_output

    test_command = task.get("test_command", "pytest -v --tb=no")
    language = task.get("repo_language", "")

    if "PATCH_APPLY_FAILED" in stdout:
        if "TEST_PATCH_APPLY_FAILED" in stdout:
            return {"score": 0.0, "test_stats": {"error": "test_patch apply failed"}}
        if "AUGMENTED_PATCH_APPLY_FAILED" in stdout:
            return {"score": 0.0, "test_stats": {"error": "augmented_test_patch apply failed"}}
        return {"score": 0.0, "test_stats": {"error": "patch apply failed"}}

    if STDOUT_BEGIN not in stdout or STDERR_BEGIN not in stdout:
        return {
            "score": 0.0,
            "test_stats": {
                "error": "No output markers",
                "container_stdout_tail": stdout[-1000:],
                "container_stderr_tail": stderr[-1000:],
            },
        }

    test_stdout = stdout[stdout.index(STDOUT_BEGIN) + len(STDOUT_BEGIN): stdout.index(STDOUT_END)].strip()
    test_stderr = stdout[stdout.index(STDERR_BEGIN) + len(STDERR_BEGIN): stdout.index(STDERR_END)].strip()
    passed_tests, failed_tests = parse_test_output(test_stdout, test_stderr, language, test_command)
    failure_details = extract_failure_details(test_stdout)

    fail_to_pass = ensure_list(task.get("fail_to_pass", []))
    pass_to_pass = ensure_list(task.get("pass_to_pass", []))
    f2p = set(fail_to_pass)
    p2p = set(pass_to_pass)
    all_required = f2p | p2p

    if not passed_tests and not failed_tests:
        summary_m = re.search(r"(\d+) runs?.*?(\d+) failures?.*?(\d+) errors?", test_stdout + test_stderr)
        if summary_m:
            total = int(summary_m.group(1))
            failures = int(summary_m.group(2))
            errors = int(summary_m.group(3))
            if total > 0 and failures == 0 and errors == 0:
                passed_tests = all_required.copy()

    canary_result: dict[str, Any] = {"enabled": bool(canary)}
    if canary:
        subverted, reason = verify_canary(canary["canaries"], passed_tests, failed_tests)
        canary_result.update({"subverted": subverted, "reason": reason, "names": [c["name"] for c in canary["canaries"]]})
        if subverted:
            return {"score": 0.0, "test_stats": {"error": f"canary_subverted: {reason}", "canary": canary_result}}

    f2p_passed = len(f2p & passed_tests)
    all_passed_count = len(all_required & passed_tests)
    all_pass = all_required <= passed_tests

    test_stats: dict[str, Any] = {
        "f2p_result": f"{f2p_passed}/{len(f2p)}",
        "all_result": f"{all_passed_count}/{len(all_required)}",
        "all_passed": all_pass,
        "canary": canary_result,
        "passed_count": len(passed_tests),
        "failed_count": len(failed_tests),
    }
    if not all_pass:
        test_stats["missing_tests"] = sorted(all_required - passed_tests)
        if failure_details:
            test_stats["failure_details"] = failure_details

    return {
        "score": 1.0 if all_pass else 0.0,
        "test_stats": test_stats,
        "test_stdout_tail": test_stdout[-2000:],
        "test_stderr_tail": test_stderr[-2000:],
    }


def extract_failure_details(test_stdout: str, limit: int = 10) -> list[dict[str, str]]:
    """Extract concise RSpec JSON failure details for retry prompts."""
    try:
        payload = json.loads(test_stdout)
    except json.JSONDecodeError:
        return []
    examples = payload.get("examples", []) if isinstance(payload, dict) else []
    details: list[dict[str, str]] = []
    for example in examples:
        if not isinstance(example, dict) or example.get("status") != "failed":
            continue
        exception = example.get("exception") if isinstance(example.get("exception"), dict) else {}
        detail = {
            "full_description": str(example.get("full_description") or example.get("description") or ""),
            "file_path": str(example.get("file_path") or ""),
            "line_number": str(example.get("line_number") or ""),
            "message": str(exception.get("message") or "")[-4000:],
        }
        backtrace = exception.get("backtrace")
        if isinstance(backtrace, list) and backtrace:
            detail["backtrace"] = "\n".join(str(item) for item in backtrace[:5])
        details.append(detail)
        if len(details) >= limit:
            break
    return details


def run_task(args: argparse.Namespace) -> dict[str, Any]:
    from cubesandbox import Sandbox

    task = json.loads(Path(args.task_json).read_text(encoding="utf-8"))
    fix_patch = Path(args.fix_patch).read_text(encoding="utf-8")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / f"affinetes-cubesandbox-{task.get('instance_id', 'task')}-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    full_script, canary = build_affinetes_full_script(task, fix_patch)
    write_local_verifier_inputs(run_dir, full_script)
    (run_dir / "task.json").write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "fix_patch.diff").write_text(fix_patch, encoding="utf-8")
    if canary:
        (run_dir / "canary.json").write_text(json.dumps(canary, indent=2, ensure_ascii=False), encoding="utf-8")

    result: dict[str, Any] = {
        "status": "started",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "template_id": args.template_id,
        "instance_id": task.get("instance_id"),
        "dockerhub_tag": task.get("dockerhub_tag"),
        "scaffold": "affinetes/environments/SWE-INFINITE/env.py::_verify",
        "restore_gate": "CubeSandbox pauses the prepared task sandbox, reconnects it, then runs the verifier script.",
        "transport": "cubecli-exec:/workspace/cubesandbox-swe",
    }

    sb = None
    try:
        sb = Sandbox.create(template=args.template_id, timeout=args.timeout)
        result["sandbox_id"] = sb.sandbox_id
        executor = CubeSandboxExecutor(
            sb.sandbox_id,
            cubecli=CUBECLI,
            workdir="/app",
            default_timeout=max(args.verify_timeout, 120),
        )
        upload_verifier_assets(executor, full_script)
        ready = executor.run(f"date -u +%FT%TZ > {sandbox_file('ready_for_save')}", timeout=args.wait_timeout)
        if ready["exit_code"] != 0:
            raise RuntimeError(f"failed to prepare verifier sandbox: {ready['stderr'] or ready['stdout']}")
        result["state_before_save"] = sb.get_info().get("state")
        state = save_sandbox_state(sb, timeout=180, interval=1)
        result["saved_state"] = {"sandbox_id": state.sandbox_id, "template_id": state.template_id}
        result["state_after_save"] = sb.get_info().get("state")
        sb = restore_sandbox_state(state, sandbox_cls=Sandbox)
        result["state_after_restore"] = sb.get_info().get("state")
        executor = CubeSandboxExecutor(
            sb.sandbox_id,
            cubecli=CUBECLI,
            workdir="/app",
            default_timeout=max(args.verify_timeout, 120),
        )
        verifier_run = run_verifier_script(executor, timeout=args.verify_timeout + 30)
        stdout = str(verifier_run["stdout"])
        stderr = str(verifier_run["stderr"])
        (run_dir / "container_stdout.log").write_text(stdout, encoding="utf-8")
        (run_dir / "container_stderr.log").write_text(stderr, encoding="utf-8")
        (run_dir / "affinetes_exit_code").write_text(str(verifier_run["affinetes_exit_code"]), encoding="utf-8")
        parsed = parse_affinetes_output(task, stdout, stderr, canary)
        result.update(parsed)
        result["affinetes_exit_code"] = str(verifier_run["affinetes_exit_code"])
        result["cubecli_exit_code"] = verifier_run["exit_code"]
        result["status"] = (
            "ok"
            if result.get("score") == 1.0
            and result.get("state_after_save") == "paused"
            and result.get("state_after_restore") == "running"
            else "failed"
        )
    except Exception as exc:
        import traceback

        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback_tail"] = traceback.format_exc().splitlines()[-8:]
    finally:
        if sb is not None:
            try:
                sb.kill()
                result["cleanup"] = "killed"
            except Exception as exc:
                result["cleanup_error"] = f"{type(exc).__name__}: {exc}"

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"affinetes_cubesandbox_swe_e2e_{run_id}.json"
    latest = RESULTS_DIR / "affinetes_cubesandbox_swe_e2e_latest.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    latest.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["result_file"] = str(out_path)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-json", default=str(DEFAULT_TASK_JSON))
    parser.add_argument("--fix-patch", default=str(DEFAULT_FIX_PATCH))
    parser.add_argument("--template-id", default=DEFAULT_TEMPLATE_ID)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--wait-timeout", type=int, default=180)
    parser.add_argument("--verify-timeout", type=int, default=VERIFY_TIMEOUT)
    return parser.parse_args()


def main() -> int:
    configure_imports()
    local_cube_env()
    result = run_task(parse_args())
    print(json.dumps({
        "status": result["status"],
        "score": result.get("score"),
        "instance_id": result.get("instance_id"),
        "run_dir": result.get("run_dir"),
        "result_file": result.get("result_file"),
        "state_after_save": result.get("state_after_save"),
        "state_after_restore": result.get("state_after_restore"),
        "test_stats": result.get("test_stats"),
    }, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
