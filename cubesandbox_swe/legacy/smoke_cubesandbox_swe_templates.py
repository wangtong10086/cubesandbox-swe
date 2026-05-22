#!/usr/bin/env python3
"""Non-destructive smoke tests for ready CubeSandbox SWE templates."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
STATE_PATH = BASE_DIR / "results" / "cubesandbox_swe_templates.json"
OUT_PATH = BASE_DIR / "results" / "cubesandbox_swe_smoke_results.json"
SDK_PATH = BASE_DIR / "third_party" / "CubeSandbox" / "sdk" / "python"
CUBECLI = "/usr/local/services/cubetoolbox/Cubelet/bin/cubecli"


@dataclass(frozen=True)
class SmokeCommandResult:
    exit_code: int
    stdout: str
    stderr: str = ""


def configure_environment() -> None:
    sys.path.insert(0, str(SDK_PATH))
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
    os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
    os.environ.setdefault("CUBE_API_URL", "http://127.0.0.1:3000")
    os.environ.setdefault("CUBE_PROXY_NODE_IP", "127.0.0.1")


def load_ready_items(state_path: Path, selected_templates: list[str]) -> list[tuple[str, str]]:
    state: dict[str, Any] = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    if selected_templates:
        by_template = {
            item.get("template_id"): (image, item.get("template_id"))
            for image, item in state.items()
            if item.get("template_id")
        }
        return [by_template.get(template_id, (template_id, template_id)) for template_id in selected_templates]

    items = [
        (image, item["template_id"])
        for image, item in state.items()
        if item.get("status") == "READY" and item.get("template_id")
    ]
    items.sort(key=lambda x: x[1])
    return items


def run_sdk_command(sandbox: Any, command: str, timeout: int) -> SmokeCommandResult:
    cmd = sandbox.commands.run(command, timeout=min(30, timeout))
    return SmokeCommandResult(exit_code=cmd.exit_code, stdout=cmd.stdout, stderr=cmd.stderr)


def run_cubecli_command(sandbox_id: str, command: str, timeout: int, cubecli: str = CUBECLI) -> SmokeCommandResult:
    proc = subprocess.run(
        [
            "sudo",
            cubecli,
            "exec",
            sandbox_id,
            "/bin/bash",
            "-lc",
            command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=min(60, timeout),
    )
    return SmokeCommandResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def run_smoke(
    image: str,
    template_id: str,
    *,
    timeout: int,
    command: str,
    exec_backend: str,
    cubecli: str,
) -> dict[str, Any]:
    configure_environment()
    from cubesandbox import Sandbox  # noqa: PLC0415

    start = time.monotonic()
    result: dict[str, Any] = {
        "image": image,
        "template_id": template_id,
    }
    sandbox = None
    try:
        sandbox = Sandbox.create(template=template_id, timeout=timeout)
        result["sandbox_id"] = sandbox.sandbox_id
        result["exec_backend"] = exec_backend
        if exec_backend == "sdk":
            cmd = run_sdk_command(sandbox, command, timeout)
        else:
            cmd = run_cubecli_command(sandbox.sandbox_id, command, timeout, cubecli)
        result.update(
            {
                "exit_code": cmd.exit_code,
                "stdout": cmd.stdout[-2000:],
                "stderr": cmd.stderr[-2000:],
                "status": "ok" if cmd.exit_code == 0 else "failed",
            }
        )
    except Exception as exc:  # noqa: BLE001 - smoke output should preserve backend errors.
        result.update({"status": "error", "error": repr(exc)})
    finally:
        if sandbox is not None:
            try:
                sandbox.kill()
                result["destroyed"] = True
            except Exception as exc:  # noqa: BLE001
                result["destroyed"] = False
                result["destroy_error"] = repr(exc)
        result["seconds"] = round(time.monotonic() - start, 3)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, default=STATE_PATH)
    parser.add_argument("--out-path", type=Path, default=OUT_PATH)
    parser.add_argument("--template-id", action="append", default=[], help="template id to smoke; repeatable")
    parser.add_argument("--limit", type=int, default=None, help="maximum number of ready templates to smoke")
    parser.add_argument("--timeout", type=int, default=120, help="sandbox TTL and create timeout hint")
    parser.add_argument("--command", default="pwd", help="shell command to run inside the sandbox")
    parser.add_argument(
        "--exec-backend",
        choices=("cubecli", "sdk"),
        default="cubecli",
        help="execution backend for the smoke command; cubecli matches the SWE e2e path",
    )
    parser.add_argument("--cubecli", default=CUBECLI, help="path to cubecli when --exec-backend=cubecli")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    items = load_ready_items(args.state_path, args.template_id)
    if args.limit is not None:
        items = items[: args.limit]
    if not items:
        print("no ready templates to smoke", flush=True)
        return 1

    args.out_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for idx, (image, template_id) in enumerate(items, 1):
        print(f"[{idx:02d}/{len(items)}] {template_id}", flush=True)
        result = run_smoke(
            image,
            template_id,
            timeout=args.timeout,
            command=args.command,
            exec_backend=args.exec_backend,
            cubecli=args.cubecli,
        )
        results.append(result)
        args.out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"    {result['status']} {result.get('seconds')}s", flush=True)

    return 0 if all(item.get("status") == "ok" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
