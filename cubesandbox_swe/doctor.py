"""Repository and runtime diagnostics for cubesandbox-swe."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from cubesandbox_swe.cubesandbox_mcp import CubeSandboxExecutor


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = BASE_DIR / "results" / "cubesandbox_swe_templates.json"
DEFAULT_SMOKE_OUT = BASE_DIR / "results" / "doctor_smoke.json"
DEFAULT_CUBECLI = "/usr/local/services/cubetoolbox/Cubelet/bin/cubecli"
SNAPSHOT_FLAG = Path("/data/cube-shim/snapshot")


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def check_path(name: str, path: Path, *, warn_only: bool = False) -> Check:
    if path.exists():
        return Check(name, "ok", str(path))
    return Check(name, "warn" if warn_only else "fail", f"missing: {path}")


def check_required_paths() -> list[Check]:
    paths = {
        "pyproject": BASE_DIR / "pyproject.toml",
        "readme": BASE_DIR / "README.md",
        "docs": BASE_DIR / "docs",
        "cubesandbox submodule": BASE_DIR / "third_party" / "CubeSandbox",
        "affinetes submodule": BASE_DIR / "third_party" / "affinetes",
        "env file": BASE_DIR / ".env",
    }
    return [
        check_path(name, path, warn_only=(name == "env file"))
        for name, path in paths.items()
    ]


def check_ready_templates(state_path: Path = DEFAULT_STATE_PATH) -> Check:
    if not state_path.exists():
        return Check("ready templates", "warn", f"state file missing: {state_path}")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return Check("ready templates", "fail", f"invalid JSON: {exc}")
    ready = [item for item in state.values() if isinstance(item, dict) and item.get("status") == "READY"]
    if not ready:
        return Check("ready templates", "warn", f"0 READY templates in {state_path}")
    return Check("ready templates", "ok", f"{len(ready)} READY templates")


def check_cube_api(timeout: float) -> Check:
    api_url = os.environ.get("CUBE_API_URL", "http://127.0.0.1:3000").rstrip("/")
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            resp = client.get(f"{api_url}/health")
        if resp.status_code >= 400:
            return Check("cube api health", "fail", f"{api_url}/health returned HTTP {resp.status_code}")
        return Check("cube api health", "ok", f"{api_url}/health")
    except Exception as exc:  # noqa: BLE001 - diagnostics should preserve backend failure.
        return Check("cube api health", "fail", f"{type(exc).__name__}: {exc}")


def check_runtime_files(cubecli: str) -> list[Check]:
    return [
        check_path("cubecli", Path(cubecli)),
        check_path("cube shim snapshot flag", SNAPSHOT_FLAG),
    ]


def first_ready_template(state_path: Path = DEFAULT_STATE_PATH) -> tuple[str, str] | None:
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    items = [
        (image, item["template_id"])
        for image, item in state.items()
        if isinstance(item, dict) and item.get("status") == "READY" and item.get("template_id")
    ]
    items.sort(key=lambda item: item[1])
    return items[0] if items else None


def check_template_smoke(args: argparse.Namespace) -> Check:
    from cubesandbox_swe.legacy import smoke_cubesandbox_swe_templates as smoke

    selected = (args.smoke_template, args.smoke_template) if args.smoke_template else first_ready_template(args.state_path)
    if selected is None:
        return Check("template smoke", "fail", f"no READY template in {args.state_path}")
    image, template_id = selected
    result = smoke.run_smoke(
        image,
        template_id,
        timeout=int(args.timeout),
        command=args.smoke_command,
        exec_backend="cubecli",
        cubecli=args.cubecli,
    )
    args.smoke_out.parent.mkdir(parents=True, exist_ok=True)
    args.smoke_out.write_text(json.dumps([result], indent=2, ensure_ascii=False), encoding="utf-8")
    status = "ok" if result.get("status") == "ok" else "fail"
    detail = f"{template_id}: {result.get('status')} ({args.smoke_out})"
    if result.get("error"):
        detail += f": {result['error']}"
    return Check("template smoke", status, detail)


def check_model_preflight(args: argparse.Namespace) -> Check:
    from cubesandbox_swe.legacy import run_cubesandbox_codex_swe_e2e as e2e

    runtime_args = SimpleNamespace(
        output_dir=str(args.output_dir),
        runs_dir=str(args.runs_dir),
        solve_template=args.solve_template,
        wire_api=args.wire_api,
        reasoning_effort=args.reasoning_effort,
        codex_http_proxy=args.codex_http_proxy,
        model=args.model,
        skip_model_preflight=False,
        model_preflight_timeout=args.model_preflight_timeout,
    )
    e2e.configure_environment(runtime_args)
    try:
        result = e2e.preflight_codex_model(runtime_args)
    except Exception as exc:  # noqa: BLE001 - diagnostics should surface exact model/provider issue.
        return Check("model preflight", "fail", f"{type(exc).__name__}: {exc}")
    return Check("model preflight", "ok", json.dumps(result, sort_keys=True))


def check_codex_runtime_smoke(args: argparse.Namespace) -> Check:
    from cubesandbox_swe.legacy import smoke_cubesandbox_swe_templates as smoke

    smoke.configure_environment()
    from cubesandbox import Sandbox

    sb = None
    try:
        sb = Sandbox.create(template=args.solve_template, timeout=int(args.timeout) + 120)
        executor = CubeSandboxExecutor(
            sb.sandbox_id,
            cubecli=args.cubecli,
            workdir="/app",
            default_timeout=max(int(args.timeout), 30),
        )
        result = executor.run("pwd && test -d .git", timeout=max(int(args.timeout), 30))
        if result["exit_code"] != 0:
            return Check(
                "codex runtime smoke",
                "fail",
                f"{args.solve_template}: {result['stderr'] or result['stdout']}",
            )
        if "/app" not in result["stdout"]:
            return Check("codex runtime smoke", "fail", f"{args.solve_template}: no stdout from CubeSandbox command")
        return Check("codex runtime smoke", "ok", f"{args.solve_template}: CubeSandbox /app command path")
    except Exception as exc:  # noqa: BLE001 - diagnostics should surface backend failure.
        return Check("codex runtime smoke", "fail", f"{type(exc).__name__}: {exc}")
    finally:
        if sb is not None:
            try:
                sb.kill()
            except Exception:
                pass


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(BASE_DIR / ".env")
    checks = []
    checks.extend(check_required_paths())
    checks.append(check_ready_templates(args.state_path))

    if args.runtime:
        checks.extend(check_runtime_files(args.cubecli))
        checks.append(check_cube_api(args.timeout))
    if args.runtime_smoke:
        checks.append(check_template_smoke(args))
    if args.codex_runtime_smoke:
        checks.append(check_codex_runtime_smoke(args))
    if args.model is not None:
        checks.append(check_model_preflight(args))

    return {
        "status": "fail" if any(check.status == "fail" for check in checks) else "ok",
        "checks": [asdict(check) for check in checks],
    }


def print_report(report: dict[str, Any]) -> None:
    for check in report["checks"]:
        label = check["status"].upper()
        detail = f" - {check['detail']}" if check.get("detail") else ""
        print(f"[{label}] {check['name']}{detail}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="store_true", help="check local CubeSandbox runtime health")
    parser.add_argument("--runtime-smoke", action="store_true", help="run one non-destructive template smoke")
    parser.add_argument(
        "--codex-runtime-smoke",
        action="store_true",
        help="run one CubeSandbox /app command smoke for the Codex MCP runtime",
    )
    parser.add_argument(
        "--model",
        nargs="?",
        const="",
        default=None,
        help="run Codex/model preflight with this model; omit the value to use .env/defaults",
    )
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--smoke-template", default="")
    parser.add_argument("--smoke-command", default="pwd")
    parser.add_argument("--smoke-out", type=Path, default=DEFAULT_SMOKE_OUT)
    parser.add_argument("--cubecli", default=DEFAULT_CUBECLI)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output-dir", type=Path, default=BASE_DIR / "results" / "doctor")
    parser.add_argument("--runs-dir", type=Path, default=BASE_DIR / "swe-e2e-runs" / "doctor")
    parser.add_argument("--solve-template", default="swe-task1-rubocop-runner-v1")
    parser.add_argument("--wire-api", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--codex-http-proxy", default=None)
    parser.add_argument("--model-preflight-timeout", type=int, default=60)
    parser.add_argument("--json", type=Path, default=None, help="write JSON report to this path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if args.runtime_smoke:
        args.runtime = True
    if args.codex_runtime_smoke:
        args.runtime = True
    report = build_report(args)
    print_report(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["status"] == "ok" else 1
