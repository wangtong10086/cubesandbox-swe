#!/usr/bin/env python3
"""Create and manage a Targon rental for Qwen3.6-27B inference."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


BASE_URL = "https://api.targon.com/tha/v2"

PROJECT_NAME = "qwen36-inference"
APP_NAME = "qwen36-rental"
WORKLOAD_NAME = "qwen36-vllm-rental"
VOLUME_NAME = "qwen36-hf-cache"

MODEL_NAME = "Qwen/Qwen3.6-27B"
VLLM_IMAGE = "vllm/vllm-openai:v0.20.1"
SGLANG_IMAGE = "lmsysorg/sglang:v0.5.12-runtime"
H200_RESOURCE = "h200-small"
STORAGE_RESOURCE = "storage-rentals"
VOLUME_SIZE_MB = 307_200
PORT = 8000
HF_CACHE_PATH = "/root/.cache/huggingface"


class TargonError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    token: str
    openai_api_key: str
    ssh_key_path: pathlib.Path
    ssh_public_key: str
    env: dict[str, str]


def load_env(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def merged_env(dotenv_path: pathlib.Path) -> dict[str, str]:
    env = dict(load_env(dotenv_path))
    env.update(os.environ)
    return env


def resolve_ssh_key(env: dict[str, str]) -> tuple[pathlib.Path, str]:
    explicit = env.get("TARGON_SSH_KEY_PATH")
    candidates: list[pathlib.Path] = []
    if explicit:
        candidates.append(pathlib.Path(explicit).expanduser())
    candidates.extend(
        [
            pathlib.Path("~/.ssh/id_ed25519").expanduser(),
            pathlib.Path("~/.ssh/targon_qwen36_ed25519").expanduser(),
            pathlib.Path("~/.ssh/id_rsa").expanduser(),
        ]
    )
    for private_key in candidates:
        public_key = pathlib.Path(f"{private_key}.pub")
        if private_key.exists() and public_key.exists():
            return private_key, public_key.read_text().strip()

    private_key = pathlib.Path("~/.ssh/targon_qwen36_ed25519").expanduser()
    private_key.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(private_key),
            "-C",
            "targon-qwen36-rental",
        ],
        check=True,
    )
    return private_key, pathlib.Path(f"{private_key}.pub").read_text().strip()


def load_config(dotenv_path: pathlib.Path) -> Config:
    env = merged_env(dotenv_path)
    token = env.get("TARGON_API_KEY") or env.get("TARGON_APIKEY")
    if not token:
        raise TargonError("Missing TARGON_API_KEY or TARGON_APIKEY in environment/.env")
    openai_api_key = env.get("OPENAI_API_KEY")
    if not openai_api_key:
        raise TargonError("Missing OPENAI_API_KEY in environment/.env")
    ssh_key_path, ssh_public_key = resolve_ssh_key(env)
    return Config(
        token=token,
        openai_api_key=openai_api_key,
        ssh_key_path=ssh_key_path,
        ssh_public_key=ssh_public_key,
        env=env,
    )


def request_json(
    cfg: Config,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> Any:
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    data = None
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise TargonError(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc
    if not raw:
        return None
    return json.loads(raw)


def paged_list(cfg: Config, path: str, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    query = dict(query or {})
    query.setdefault("limit", 100)
    items: list[dict[str, Any]] = []
    cursor = None
    while True:
        if cursor:
            query["after"] = cursor
        page = request_json(cfg, "GET", path, query=query)
        if isinstance(page, list):
            items.extend(page)
            return items
        if not isinstance(page, dict):
            raise TargonError(f"Unexpected list response from {path}: {type(page).__name__}")
        items.extend(page.get("items", []))
        cursor = page.get("next_cursor") or page.get("next")
        if not cursor:
            return items


def by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in items if item.get("name") == name), None)


def ensure_project(cfg: Config) -> dict[str, Any]:
    existing = by_name(paged_list(cfg, "/projects"), PROJECT_NAME)
    if existing:
        print(f"project: reuse {PROJECT_NAME} ({existing['uid']})", flush=True)
        return existing
    project = request_json(cfg, "POST", "/projects", {"name": PROJECT_NAME})
    print(f"project: created {PROJECT_NAME} ({project['uid']})", flush=True)
    return project


def ensure_app(cfg: Config, project_uid: str) -> dict[str, Any]:
    existing = by_name(paged_list(cfg, "/apps", {"project_id": project_uid}), APP_NAME)
    if existing:
        print(f"app: reuse {APP_NAME} ({existing['uid']})", flush=True)
        return existing
    app = request_json(
        cfg,
        "POST",
        "/apps",
        {"name": APP_NAME, "project_id": project_uid},
    )
    print(f"app: created {APP_NAME} ({app['uid']})", flush=True)
    return app


def ensure_volume(cfg: Config) -> dict[str, Any]:
    existing = by_name(paged_list(cfg, "/volumes"), VOLUME_NAME)
    if existing:
        state = request_json(cfg, "GET", f"/volumes/{existing['uid']}/state")
        status = str(state.get("status", "")).upper()
        message = str(state.get("message", ""))
        if status == "DELETED" or "FAIL" in status or "ERROR" in status or "FAILED" in message.upper():
            old_name = f"{VOLUME_NAME}-old-{existing['uid'][-6:]}"
            print(
                f"volume: {VOLUME_NAME} is unusable ({status}); renaming old record to {old_name}",
                flush=True,
            )
            request_json(cfg, "PATCH", f"/volumes/{existing['uid']}", {"name": old_name})
        else:
            print(f"volume: reuse {VOLUME_NAME} ({existing['uid']})", flush=True)
            return existing
    existing = by_name(paged_list(cfg, "/volumes"), VOLUME_NAME)
    if existing:
        print(f"volume: reuse {VOLUME_NAME} ({existing['uid']})", flush=True)
        return existing
    volume = request_json(
        cfg,
        "POST",
        "/volumes",
        {
            "name": VOLUME_NAME,
            "size_in_mb": VOLUME_SIZE_MB,
            "resource_name": STORAGE_RESOURCE,
        },
    )
    print(f"volume: created {VOLUME_NAME} ({volume['uid']})", flush=True)
    return volume


def wait_volume(cfg: Config, volume_uid: str, timeout_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        state = request_json(cfg, "GET", f"/volumes/{volume_uid}/state")
        status = str(state.get("status", "")).upper()
        print(f"volume state: {status or 'UNKNOWN'}", flush=True)
        if status in {"REGISTERED", "READY", "AVAILABLE", "RUNNING"}:
            return
        if "FAIL" in status or "ERROR" in status:
            raise TargonError(f"Volume {volume_uid} failed: {state}")
        if time.monotonic() >= deadline:
            raise TargonError(f"Timed out waiting for volume {volume_uid}")
        time.sleep(10)


def ensure_ssh_key(cfg: Config) -> dict[str, Any]:
    keys = paged_list(cfg, "/ssh-keys")
    for key in keys:
        if key.get("public_key") == cfg.ssh_public_key or key.get("public_key_raw") == cfg.ssh_public_key:
            print(f"ssh key: reuse ({key['uid']})", flush=True)
            return key
    name = f"qwen36-{cfg.ssh_key_path.name}"
    key = request_json(
        cfg,
        "POST",
        "/ssh-keys",
        {"name": name, "ssh_key": cfg.ssh_public_key},
    )
    print(f"ssh key: created ({key['uid']})", flush=True)
    return key


def h200_available(cfg: Config) -> bool:
    inventory = request_json(
        cfg,
        "GET",
        "/inventory",
        query={"type": "rental", "gpu": "true"},
    )
    for item in inventory:
        if item.get("name") == H200_RESOURCE:
            available = int(item.get("available") or 0)
            print(f"inventory: {H200_RESOURCE} available={available}", flush=True)
            return available > 0
    raise TargonError(f"{H200_RESOURCE} not found in Targon rental inventory")


def desired_envs(cfg: Config) -> list[dict[str, str]]:
    envs = [
        {"name": "HF_HOME", "value": HF_CACHE_PATH},
        {"name": "VLLM_CACHE_ROOT", "value": f"{HF_CACHE_PATH}/vllm-cache"},
        {"name": "VLLM_API_KEY", "value": cfg.openai_api_key},
        {"name": "OPENAI_API_KEY", "value": cfg.openai_api_key},
    ]
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if cfg.env.get(key):
            envs.append({"name": key, "value": cfg.env[key]})
    return envs


def debug_command() -> tuple[list[str], list[str]]:
    return ["/bin/bash", "-lc"], [
        f"mkdir -p {HF_CACHE_PATH} && echo 'debug container ready' && tail -f /dev/null"
    ]


def vllm_command() -> tuple[list[str], list[str]]:
    script = f"""
set -euo pipefail
export HF_HOME={HF_CACHE_PATH}
python3 - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("{MODEL_NAME}", max_workers=8)
PY
exec vllm serve {MODEL_NAME} \\
  --host 0.0.0.0 \\
  --port {PORT} \\
  --served-model-name {MODEL_NAME} \\
  --tensor-parallel-size 1 \\
  --max-model-len 262144 \\
  --reasoning-parser qwen3 \\
  --enable-auto-tool-choice \\
  --tool-call-parser qwen3_coder \\
  --language-model-only
""".strip()
    return ["/bin/bash", "-lc"], [script]


def create_workload_body(
    project_uid: str,
    app_uid: str,
    volume_uid: str,
    ssh_key_uid: str,
    cfg: Config,
    final: bool,
) -> dict[str, Any]:
    commands, args = vllm_command() if final else debug_command()
    return {
        "name": WORKLOAD_NAME,
        "project_id": project_uid,
        "app_id": app_uid,
        "type": "RENTAL",
        "resource_name": H200_RESOURCE,
        "image": VLLM_IMAGE,
        "commands": commands,
        "args": args,
        "envs": desired_envs(cfg),
        "ports": [
            {
                "port": PORT,
                "protocol": "TCP",
                "routing": "PROXIED",
            }
        ],
        "volumes": [
            {
                "uid": volume_uid,
                "mount_path": HF_CACHE_PATH,
                "read_only": False,
            }
        ],
        "ssh_keys": [ssh_key_uid],
    }


def find_workload(cfg: Config, project_uid: str) -> dict[str, Any] | None:
    workloads = paged_list(
        cfg,
        "/workloads",
        {"project_id": project_uid, "type": "RENTAL"},
    )
    return by_name(workloads, WORKLOAD_NAME)


def ensure_workload(
    cfg: Config,
    project_uid: str,
    app_uid: str,
    volume_uid: str,
    ssh_key_uid: str,
    final: bool,
) -> dict[str, Any]:
    existing = find_workload(cfg, project_uid)
    if existing:
        print(f"workload: reuse {WORKLOAD_NAME} ({existing['uid']})", flush=True)
        return existing
    if not h200_available(cfg):
        raise TargonError(f"No available {H200_RESOURCE} rental capacity right now")
    body = create_workload_body(project_uid, app_uid, volume_uid, ssh_key_uid, cfg, final=final)
    workload = request_json(cfg, "POST", "/workloads", body)
    print(f"workload: created {WORKLOAD_NAME} ({workload['uid']})", flush=True)
    return workload


def deploy_workload(cfg: Config, workload_uid: str) -> None:
    request_json(cfg, "POST", f"/workloads/{workload_uid}/deploy")
    print(f"workload: deploy requested ({workload_uid})", flush=True)


def workload_state(cfg: Config, workload_uid: str) -> dict[str, Any]:
    return request_json(cfg, "GET", f"/workloads/{workload_uid}/state")


def wait_workload(cfg: Config, workload_uid: str, timeout_s: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_status = ""
    while True:
        state = workload_state(cfg, workload_uid)
        status = str(state.get("status", "")).upper()
        message = str(state.get("message", ""))
        if status != last_status:
            print(f"workload state: {status or 'UNKNOWN'}", flush=True)
            last_status = status
        else:
            print(f"workload state: {status or 'UNKNOWN'}", flush=True)
        if status == "RUNNING":
            return state
        transient_error = (
            status == "ERROR"
            and any(
                marker in message
                for marker in (
                    "Waiting for volume",
                    "Scheduling",
                    "Container started",
                    "Deployment initiated",
                )
            )
        )
        if ("FAIL" in status or "ERROR" in status) and not transient_error:
            raise TargonError(f"Workload {workload_uid} failed: {state}")
        if time.monotonic() >= deadline:
            raise TargonError(f"Timed out waiting for workload {workload_uid}")
        time.sleep(15)


def patch_workload_final(
    cfg: Config,
    workload_uid: str,
    volume_uid: str,
    ssh_key_uid: str,
) -> dict[str, Any]:
    commands, args = vllm_command()
    body = {
        "image": VLLM_IMAGE,
        "commands": commands,
        "args": args,
        "envs": desired_envs(cfg),
        "ports": [
            {
                "port": PORT,
                "protocol": "TCP",
                "routing": "PROXIED",
            }
        ],
        "volumes": [
            {
                "uid": volume_uid,
                "mount_path": HF_CACHE_PATH,
                "read_only": False,
            }
        ],
        "ssh_keys": [ssh_key_uid],
    }
    updated = request_json(cfg, "PATCH", f"/workloads/{workload_uid}", body)
    print(f"workload: patched final vLLM command ({workload_uid})", flush=True)
    return updated


def endpoint_from_state(state: dict[str, Any]) -> str | None:
    urls = state.get("urls") or []
    for entry in urls:
        if int(entry.get("port") or 0) == PORT and entry.get("url"):
            return str(entry["url"]).rstrip("/")
    return None


def print_access(workload_uid: str, state: dict[str, Any], ssh_key_path: pathlib.Path) -> None:
    endpoint = endpoint_from_state(state)
    print("", flush=True)
    print(f"workload_uid={workload_uid}", flush=True)
    if endpoint:
        print(f"endpoint={endpoint}/v1", flush=True)
    else:
        print("endpoint=<not reported yet>", flush=True)
    print(
        "ssh=ssh -o StrictHostKeyChecking=accept-new "
        f"-i {ssh_key_path} {workload_uid}@ssh.deployments.targon.com",
        flush=True,
    )


def update_env_file(path: pathlib.Path, endpoint: str) -> None:
    values = load_env(path)
    values["OPENAI_BASE_URL"] = f"{endpoint.rstrip('/')}/v1"
    values["OPENAI_MODEL"] = MODEL_NAME
    lines: list[str] = []
    seen: set[str] = set()
    if path.exists():
        for raw_line in path.read_text().splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                lines.append(raw_line)
                continue
            key = stripped
            if key.startswith("export "):
                key = key[len("export ") :].strip()
            key = key.split("=", 1)[0].strip()
            if key in {"OPENAI_BASE_URL", "OPENAI_MODEL"}:
                lines.append(f"{key}={values[key]}")
                seen.add(key)
            else:
                lines.append(raw_line)
    for key in ("OPENAI_BASE_URL", "OPENAI_MODEL"):
        if key not in seen:
            lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n")
    print(f"env: updated {path} OPENAI_BASE_URL and OPENAI_MODEL", flush=True)


def prepare(cfg: Config, wait_timeout_s: int, final: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    project = ensure_project(cfg)
    app = ensure_app(cfg, project["uid"])
    volume = ensure_volume(cfg)
    wait_volume(cfg, volume["uid"], timeout_s=wait_timeout_s)
    ssh_key = ensure_ssh_key(cfg)
    workload = ensure_workload(
        cfg,
        project["uid"],
        app["uid"],
        volume["uid"],
        ssh_key["uid"],
        final=final,
    )
    return workload, {"project": project, "app": app, "volume": volume, "ssh_key": ssh_key}


def cmd_deploy_debug(args: argparse.Namespace) -> None:
    cfg = load_config(args.env_file)
    workload, resources = prepare(cfg, args.wait_timeout_s, final=False)
    try:
        deploy_workload(cfg, workload["uid"])
    except TargonError as exc:
        if "HTTP 502" not in str(exc):
            raise
        print(f"workload: deploy returned transient 502; continuing to poll ({workload['uid']})", flush=True)
    state = wait_workload(cfg, workload["uid"], args.wait_timeout_s)
    print_access(workload["uid"], state, cfg.ssh_key_path)
    if args.update_env:
        endpoint = endpoint_from_state(state)
        if endpoint:
            update_env_file(args.env_file, endpoint)
    print(f"volume_uid={resources['volume']['uid']}", flush=True)


def cmd_deploy_final(args: argparse.Namespace) -> None:
    cfg = load_config(args.env_file)
    workload, _resources = prepare(cfg, args.wait_timeout_s, final=True)
    try:
        deploy_workload(cfg, workload["uid"])
    except TargonError as exc:
        if "HTTP 502" not in str(exc):
            raise
        print(f"workload: deploy returned transient 502; continuing to poll ({workload['uid']})", flush=True)
    state = wait_workload(cfg, workload["uid"], args.wait_timeout_s)
    print_access(workload["uid"], state, cfg.ssh_key_path)
    if args.update_env:
        endpoint = endpoint_from_state(state)
        if endpoint:
            update_env_file(args.env_file, endpoint)


def cmd_patch_final(args: argparse.Namespace) -> None:
    cfg = load_config(args.env_file)
    workload, resources = prepare(cfg, args.wait_timeout_s, final=False)
    patch_workload_final(
        cfg,
        workload["uid"],
        resources["volume"]["uid"],
        resources["ssh_key"]["uid"],
    )
    state = workload_state(cfg, workload["uid"])
    print_access(workload["uid"], state, cfg.ssh_key_path)
    if args.update_env:
        endpoint = endpoint_from_state(state)
        if endpoint:
            update_env_file(args.env_file, endpoint)


def cmd_status(args: argparse.Namespace) -> None:
    cfg = load_config(args.env_file)
    project = ensure_project(cfg)
    workload = find_workload(cfg, project["uid"])
    if not workload:
        raise TargonError(f"Workload {WORKLOAD_NAME} does not exist")
    state = workload_state(cfg, workload["uid"])
    print(json.dumps(state, indent=2, sort_keys=True), flush=True)
    print_access(workload["uid"], state, cfg.ssh_key_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=pathlib.Path,
        default=pathlib.Path(".env"),
        help="Path to dotenv file containing TARGON_APIKEY and OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--wait-timeout-s",
        type=int,
        default=3600,
        help="Maximum seconds to wait for volume/workload readiness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy_debug = subparsers.add_parser("deploy-debug")
    deploy_debug.add_argument("--update-env", action="store_true")
    deploy_debug.set_defaults(func=cmd_deploy_debug)

    deploy_final = subparsers.add_parser("deploy-final")
    deploy_final.add_argument("--update-env", action="store_true")
    deploy_final.set_defaults(func=cmd_deploy_final)

    patch_final = subparsers.add_parser("patch-final")
    patch_final.add_argument("--update-env", action="store_true")
    patch_final.set_defaults(func=cmd_patch_final)

    status = subparsers.add_parser("status")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except TargonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
