#!/usr/bin/env python3
"""Collect repeated CubeSandbox+Codex SWE-INFINITE trajectories for 50 tasks."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_IMAGES_PATH = BASE_DIR / "results" / "swe_infinite_images_50_results.json"
DEFAULT_TEMPLATES_PATH = BASE_DIR / "results" / "cubesandbox_swe_templates.json"
DEFAULT_OUTPUT_ROOT = BASE_DIR / "results" / "swe50_trajectories"
RUNNER = BASE_DIR / "scripts" / "run_cubesandbox_codex_swe_e2e.py"
R2_BASE = "https://pub-7882418a56434a479bf9a7febd660b36.r2.dev/bugs"
R2_FETCH_TIMEOUT = 8


manifest_lock = threading.Lock()
INCOMPLETE_RESULT_STATUSES = {"started", "running", "missing"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def selected_images(path: Path, limit: int) -> list[dict[str, Any]]:
    data = load_json(path, [])
    images = [item for item in data if item.get("status") == "ok" and item.get("image")]
    return images[:limit]


def ready_templates(path: Path) -> dict[str, str]:
    state = load_json(path, {})
    ready: dict[str, str] = {}
    for image, entry in state.items():
        if entry.get("status") == "READY" and entry.get("template_id"):
            ready[image] = entry["template_id"]
    return ready


def task_url(task_id: int) -> str:
    return f"{R2_BASE}/task_{task_id:011d}.json"


def fetch_task(task_id: int) -> dict[str, Any] | None:
    req = urllib.request.Request(
        task_url(task_id),
        headers={"Accept": "application/json", "User-Agent": "swe50-collector/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=R2_FETCH_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        return None
    except Exception:
        return None


def task_id_from_cached(path: Path) -> int | str:
    stem = path.stem
    match = re.search(r"(\d+)$", stem)
    return int(match.group(1)) if match else stem


def load_task_index(index_path: Path, tasks_dir: Path) -> dict[str, dict[str, Any]]:
    raw = load_json(index_path, {})
    resolved: dict[str, dict[str, Any]] = {}
    for image, meta in raw.items():
        task_path = Path(meta.get("task_json", ""))
        if not task_path.is_absolute():
            task_path = tasks_dir / task_path
        if not task_path.exists():
            continue
        task = load_json(task_path, {})
        if task.get("dockerhub_tag") != image:
            continue
        resolved[image] = {
            **meta,
            "task_json": str(task_path),
            "task": task,
        }
    return resolved


def resolve_tasks(
    images: list[dict[str, Any]],
    tasks_dir: Path,
    scan_max: int,
    workers: int,
) -> dict[str, dict[str, Any]]:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    index_path = tasks_dir / "image_to_task.json"
    wanted = {item["image"] for item in images}
    resolved = load_task_index(index_path, tasks_dir)
    unresolved = wanted - set(resolved)
    if not unresolved:
        return resolved

    existing_by_image: dict[str, Path] = {}
    for path in tasks_dir.glob("task_*.json"):
        task = load_json(path, {})
        image = task.get("dockerhub_tag")
        if image in unresolved:
            existing_by_image[image] = path
    for image, path in existing_by_image.items():
        task = load_json(path, {})
        resolved[image] = {
            "task_id": task_id_from_cached(path),
            "task_json": str(path),
            "instance_id": task.get("instance_id"),
            "dockerhub_tag": image,
            "task": task,
        }
    unresolved = wanted - set(resolved)
    if not unresolved:
        compact = {k: {kk: vv for kk, vv in v.items() if kk != "task"} for k, v in resolved.items()}
        write_json(index_path, compact)
        return resolved

    print(f"resolving tasks from R2: unresolved={len(unresolved)} scan_max={scan_max} workers={workers}", flush=True)

    seen = 0
    seen_lock = threading.Lock()

    def probe(task_id: int) -> tuple[int, dict[str, Any] | None]:
        task = fetch_task(task_id)
        return task_id, task

    batch_size = max(workers * 8, 64)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch_start in range(1, scan_max + 1, batch_size):
            if not unresolved:
                break
            batch_end = min(scan_max, batch_start + batch_size - 1)
            print(f"  scanning ids {batch_start}-{batch_end}", flush=True)
            futures = {pool.submit(probe, task_id): task_id for task_id in range(batch_start, batch_end + 1)}
            for fut in as_completed(futures):
                task_id, task = fut.result()
                with seen_lock:
                    seen += 1
                    if seen % 500 == 0:
                        print(f"  scanned={seen} resolved={len(resolved)}/{len(wanted)}", flush=True)
                if not task:
                    continue
                image = task.get("dockerhub_tag")
                if image not in unresolved:
                    continue
                out_path = tasks_dir / f"task_{task_id:011d}.json"
                write_json(out_path, task)
                resolved[image] = {
                    "task_id": task_id,
                    "task_json": str(out_path),
                    "instance_id": task.get("instance_id"),
                    "dockerhub_tag": image,
                    "task": task,
                }
                unresolved.remove(image)
                print(f"  resolved {len(resolved)}/{len(wanted)} task_id={task_id} image={image}", flush=True)
                compact = {k: {kk: vv for kk, vv in v.items() if kk != "task"} for k, v in resolved.items()}
                write_json(index_path, compact)

    if unresolved:
        missing = "\n".join(sorted(unresolved))
        raise RuntimeError(f"could not resolve {len(unresolved)} images within scan_max={scan_max}:\n{missing}")

    compact = {k: {kk: vv for kk, vv in v.items() if kk != "task"} for k, v in resolved.items()}
    write_json(index_path, compact)
    return resolved


def load_manifest(path: Path) -> dict[str, Any]:
    return load_json(path, {"created_at": utc_now(), "updated_at": utc_now(), "runs": {}})


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    write_json(path, manifest)


def status_from_result(result_path: Path) -> tuple[str, float | None, str]:
    if not result_path.exists():
        return "missing", None, "result.json missing"
    try:
        result = load_json(result_path, {})
    except Exception as exc:
        return "error", None, f"invalid result json: {exc}"
    status = result.get("status") or "unknown"
    score = ((result.get("verify") or {}).get("score"))
    error = result.get("error") or ""
    return status, score, error


def is_incomplete_result_status(status: str) -> bool:
    return status in INCOMPLETE_RESULT_STATUSES


def csv_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def standardize_outputs(run_dir: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    patterns = {
        "result_json": "cubesandbox_codex_swe_e2e_*.json",
        "trajectory_json": "cubesandbox_codex_trajectory_*.json",
        "rollout_bucket_json": "rollout_bucket_SWE-INFINITE_*.json",
    }
    for key, pattern in patterns.items():
        matches = [
            path for path in sorted(run_dir.glob(pattern))
            if not path.name.endswith("_latest.json")
        ]
        if matches:
            src = matches[-1]
            dst_name = {
                "result_json": "result.json",
                "trajectory_json": "trajectory.json",
                "rollout_bucket_json": "rollout_bucket.json",
            }[key]
            dst = run_dir / dst_name
            if src != dst:
                shutil.copy2(src, dst)
            paths[key] = str(dst)
    return paths


def copy_seeded_run(args: argparse.Namespace, job: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    run_dir = Path(job["run_dir"])
    source_run_dir = Path(job["source_run_dir"])
    result_json = run_dir / "result.json"
    if result_json.exists() and not args.force:
        status, score, error = status_from_result(result_json)
        final = {**job, "status": "skipped", "result_status": status, "score": score, "error": error}
    else:
        if run_dir.exists() and args.force:
            shutil.rmtree(run_dir)
        run_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_run_dir, run_dir, dirs_exist_ok=True)
        paths = standardize_outputs(run_dir)
        status, score, error = status_from_result(Path(paths.get("result_json", result_json)))
        final = {
            **job,
            "status": "copied",
            "returncode": 0,
            "elapsed_seconds": 0.0,
            "result_status": status,
            "score": score,
            "error": error,
            **paths,
        }
    with manifest_lock:
        manifest = load_manifest(manifest_path)
        manifest["runs"][job["job_key"]] = {
            **final,
            "finished_at": utc_now(),
        }
        save_manifest(manifest_path, manifest)
    return final


def run_one(args: argparse.Namespace, job: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    if str(job.get("source_run_policy") or "").startswith("copied_"):
        return copy_seeded_run(args, job, manifest_path)

    run_dir = Path(job["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    result_json = run_dir / "result.json"
    if result_json.exists() and not args.force:
        status, score, error = status_from_result(result_json)
        if not is_incomplete_result_status(status):
            return {**job, "status": "skipped", "result_status": status, "score": score, "error": error}

    run_id = job["run_id"]
    cmd = [
        sys.executable,
        str(RUNNER),
        "--task-json",
        job["task_json"],
        "--model",
        args.model,
        "--reasoning-effort",
        args.reasoning_effort,
        "--wire-api",
        args.wire_api,
        "--solve-template",
        job["template_id"],
        "--verify-template",
        job["template_id"],
        "--solve-timeout",
        str(args.solve_timeout),
        "--verify-timeout",
        str(args.verify_timeout),
        "--codex-location",
        "sandbox",
        "--max-solve-attempts",
        str(getattr(args, "max_solve_attempts", 1)),
        "--max-verify-attempts",
        str(getattr(args, "max_verify_attempts", 1)),
        "--model-preflight-timeout",
        str(args.model_preflight_timeout),
        "--output-dir",
        str(run_dir),
        "--runs-dir",
        str(args.output_root / "sandbox-runs"),
        "--run-id",
        run_id,
        "--rollout-task-id",
        str(job["task_id"]),
        "--rollout-model",
        args.model,
        "--rollout-model-revision",
        run_id,
        "--rollout-miner-hotkey",
        args.rollout_miner_hotkey,
        "--rollout-validator-hotkey",
        args.rollout_validator_hotkey,
    ]
    if args.skip_model_preflight:
        cmd.append("--skip-model-preflight")
    if args.codex_http_proxy:
        cmd.extend(["--codex-http-proxy", args.codex_http_proxy])

    with manifest_lock:
        manifest = load_manifest(manifest_path)
        manifest["runs"][job["job_key"]] = {
            **job,
            "status": "running",
            "started_at": utc_now(),
        }
        save_manifest(manifest_path, manifest)

    start = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=args.run_timeout,
    )
    elapsed = time.monotonic() - start
    (run_dir / "runner_stdout.log").write_text(proc.stdout, encoding="utf-8", errors="replace")
    (run_dir / "runner_stderr.log").write_text(proc.stderr, encoding="utf-8", errors="replace")
    paths = standardize_outputs(run_dir)
    status, score, error = status_from_result(Path(paths.get("result_json", run_dir / "result.json")))
    final = {
        **job,
        "status": "done" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "result_status": status,
        "score": score,
        "error": error,
        **paths,
    }
    with manifest_lock:
        manifest = load_manifest(manifest_path)
        manifest["runs"][job["job_key"]] = {
            **final,
            "finished_at": utc_now(),
        }
        save_manifest(manifest_path, manifest)
    return final


def build_jobs(args: argparse.Namespace, images: list[dict[str, Any]], templates: dict[str, str], tasks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for item in images:
        image = item["image"]
        template_id = templates[image]
        task_meta = tasks[image]
        task_id = task_meta["task_id"]
        instance_id = task_meta.get("instance_id") or f"task-{task_id}"
        for rep in range(args.repeats):
            run_id = f"task{task_id}_rep{rep}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:8]}"
            job_key = f"{task_id}:rep{rep}"
            run_dir = args.output_root / "runs" / str(task_id) / f"rep_{rep}"
            jobs.append({
                "job_key": job_key,
                "task_id": task_id,
                "instance_id": instance_id,
                "image": image,
                "template_id": template_id,
                "task_json": task_meta["task_json"],
                "rep": rep,
                "run_id": run_id,
                "run_dir": str(run_dir),
            })
    return jobs


def build_jobs_from_seed_manifest(args: argparse.Namespace) -> list[dict[str, Any]]:
    seed = load_json(args.seed_manifest, {})
    source_runs = seed.get("runs") if isinstance(seed.get("runs"), dict) else {}
    rerun_statuses = csv_set(args.rerun_result_status)
    copy_statuses = csv_set(args.copy_result_status)
    overlap = rerun_statuses & copy_statuses
    if overlap:
        raise RuntimeError(f"statuses cannot be both rerun and copied: {sorted(overlap)}")
    if not rerun_statuses and not copy_statuses:
        raise RuntimeError("--seed-manifest requires --rerun-result-status or --copy-result-status")

    jobs: list[dict[str, Any]] = []
    for job_key, source in sorted(source_runs.items()):
        result_status = str(source.get("result_status") or "")
        if result_status in copy_statuses:
            policy = "copied_success" if result_status == "ok" else "copied_model_error"
        elif result_status in rerun_statuses:
            policy = "rerun_failure"
        else:
            continue
        task_id = source.get("task_id")
        rep = source.get("rep")
        if task_id is None or rep is None:
            continue
        run_id = str(source.get("run_id") or "")
        if policy == "rerun_failure":
            run_id = f"task{task_id}_rep{rep}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:8]}"
        jobs.append({
            "job_key": str(job_key),
            "task_id": task_id,
            "instance_id": source.get("instance_id") or f"task-{task_id}",
            "image": source.get("image") or "",
            "template_id": source.get("template_id"),
            "task_json": source.get("task_json"),
            "rep": rep,
            "run_id": run_id,
            "run_dir": str(args.output_root / "runs" / str(task_id) / f"rep_{rep}"),
            "source_run_dir": source.get("run_dir"),
            "source_result_status": result_status,
            "source_run_policy": policy,
            "source_manifest": str(args.seed_manifest),
        })
    missing = [job for job in jobs if not job.get("template_id") or not job.get("task_json")]
    if missing:
        raise RuntimeError(f"{len(missing)} seeded jobs are missing template_id or task_json")
    missing_sources = [
        job for job in jobs if str(job.get("source_run_policy") or "").startswith("copied_") and not job.get("source_run_dir")
    ]
    if missing_sources:
        raise RuntimeError(f"{len(missing_sources)} copied seeded jobs are missing source_run_dir")
    return jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images-json", type=Path, default=DEFAULT_IMAGES_PATH)
    parser.add_argument("--templates-json", type=Path, default=DEFAULT_TEMPLATES_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--resolver-workers", type=int, default=64)
    parser.add_argument("--scan-max", type=int, default=50000)
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--seed-manifest", type=Path, default=None)
    parser.add_argument("--rerun-result-status", default="")
    parser.add_argument("--copy-result-status", default="")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.5"))
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--wire-api", default="responses")
    parser.add_argument("--solve-timeout", type=int, default=1800)
    parser.add_argument("--verify-timeout", type=int, default=1800)
    parser.add_argument("--run-timeout", type=int, default=4200)
    parser.add_argument("--max-solve-attempts", type=int, default=1)
    parser.add_argument("--max-verify-attempts", type=int, default=1)
    parser.add_argument("--skip-model-preflight", action="store_true")
    parser.add_argument("--model-preflight-timeout", type=int, default=90)
    parser.add_argument("--codex-http-proxy", default=os.environ.get("SWE_INFINITE_CODEX_HTTP_PROXY", ""))
    parser.add_argument("--rollout-miner-hotkey", default=os.environ.get("AFFINE_ROLLOUT_MINER_HOTKEY", "local-cubesandbox"))
    parser.add_argument("--rollout-validator-hotkey", default=os.environ.get("AFFINE_ROLLOUT_VALIDATOR_HOTKEY", "executor-SWE-INFINITE-local"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_root = args.output_root.resolve()
    tasks_dir = args.output_root / "tasks"
    manifest_path = args.output_root / "manifest.json"
    args.output_root.mkdir(parents=True, exist_ok=True)

    if args.seed_manifest:
        jobs = build_jobs_from_seed_manifest(args)
        images_json = ""
        templates_json = ""
        resolved_task_count = len({str(job["task_id"]) for job in jobs})
    else:
        images = selected_images(args.images_json, args.limit)
        if len(images) != args.limit:
            raise RuntimeError(f"selected {len(images)} images, expected {args.limit}")
        templates = ready_templates(args.templates_json)
        missing_templates = [item["image"] for item in images if item["image"] not in templates]
        if missing_templates:
            raise RuntimeError(f"{len(missing_templates)} images do not have READY templates: {missing_templates[:5]}")

        tasks = resolve_tasks(images, tasks_dir, args.scan_max, args.resolver_workers)
        jobs = build_jobs(args, images, templates, tasks)
        images_json = str(args.images_json)
        templates_json = str(args.templates_json)
        resolved_task_count = len(tasks)
    manifest = load_manifest(manifest_path)
    manifest.update({
        "output_root": str(args.output_root),
        "images_json": images_json,
        "templates_json": templates_json,
        "seed_manifest": str(args.seed_manifest) if args.seed_manifest else "",
        "rerun_result_status": args.rerun_result_status,
        "copy_result_status": args.copy_result_status,
        "limit": args.limit,
        "repeats": args.repeats,
        "concurrency": args.concurrency,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "wire_api": args.wire_api,
        "skip_model_preflight": args.skip_model_preflight,
        "model_preflight_timeout": args.model_preflight_timeout,
        "max_solve_attempts": args.max_solve_attempts,
        "max_verify_attempts": args.max_verify_attempts,
        "total_jobs": len(jobs),
    })
    save_manifest(manifest_path, manifest)

    print(f"resolved_tasks={resolved_task_count} jobs={len(jobs)} concurrency={args.concurrency}", flush=True)
    if args.resolve_only or args.dry_run:
        for job in jobs[: min(10, len(jobs))]:
            print(json.dumps(job, ensure_ascii=False), flush=True)
        return 0

    failures = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_map = {pool.submit(run_one, args, job, manifest_path): job for job in jobs}
        done = 0
        for fut in as_completed(future_map):
            done += 1
            job = future_map[fut]
            try:
                result = fut.result()
            except Exception as exc:
                failures += 1
                result = {**job, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
                with manifest_lock:
                    manifest = load_manifest(manifest_path)
                    manifest["runs"][job["job_key"]] = {**result, "finished_at": utc_now()}
                    save_manifest(manifest_path, manifest)
            if result.get("status") not in {"done", "skipped", "copied"}:
                failures += 1
            print(
                f"[{done:03d}/{len(jobs)}] task={job['task_id']} rep={job['rep']} "
                f"status={result.get('status')} result={result.get('result_status')} "
                f"score={result.get('score')} rc={result.get('returncode')}",
                flush=True,
            )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
