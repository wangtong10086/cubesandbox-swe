#!/usr/bin/env python3
"""Run Aliyun-teacher hint-eval scoring for the three existing student models."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TARGON_SCRIPT = ROOT / "scripts/targon_qwen36_rental.py"
PROXY_SCRIPT = ROOT / "scripts/qwen_responses_namespace_proxy.py"
LOCAL_BASE_URL = "http://127.0.0.1:18088/v1"
PORT = 8000


STUDENTS = [
    {
        "label": "qwen36",
        "model": "Qwen/Qwen3.6-27B",
        "old_summary": "results/hint_eval_full/qwen50_20260522T204228Z/summary.qwen.json",
        "score_file": "scores.qwen36.jsonl",
        "summary_file": "summary.qwen36.json",
        "provider_file": "provider_check.qwen36.json",
        "ablation_file": "ablation.qwen36.json",
        "ablation_md": "ablation.qwen36.md",
        "template_body": "results/hint_eval_full/qwen32_20260524T063443Z/preflight/qwen32_patch_body.redacted.json",
        "template_model": "Qwen/Qwen3-32B",
    },
    {
        "label": "affine",
        "model": "0xbidkslj2/Affine-5CFUPEUT5fkqai3SLXirnnU9Px4bdvhVNcaDAJAcvF74roRE",
        "old_summary": "results/hint_eval_full/affine50_20260523T174013Z/summary.affine.json",
        "score_file": "scores.affine.jsonl",
        "summary_file": "summary.affine.json",
        "provider_file": "provider_check.affine.json",
        "ablation_file": "ablation.affine.json",
        "ablation_md": "ablation.affine.md",
        "template_body": "results/hint_eval_full/affine50_20260523T174013Z/preflight/affine_patch_body.redacted.json",
        "template_model": "0xbidkslj2/Affine-5CFUPEUT5fkqai3SLXirnnU9Px4bdvhVNcaDAJAcvF74roRE",
    },
    {
        "label": "qwen32",
        "model": "Qwen/Qwen3-32B",
        "old_summary": "results/hint_eval_full/qwen32_20260524T063443Z/summary.qwen32.json",
        "score_file": "scores.qwen32.jsonl",
        "summary_file": "summary.qwen32.json",
        "provider_file": "provider_check.qwen32.json",
        "ablation_file": "ablation.qwen32.json",
        "ablation_md": "ablation.qwen32.md",
        "template_body": "results/hint_eval_full/qwen32_20260524T063443Z/preflight/qwen32_patch_body.redacted.json",
        "template_model": "Qwen/Qwen3-32B",
    },
]


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}", flush=True)


def load_targon_module() -> Any:
    spec = importlib.util.spec_from_file_location("targon_qwen36_rental", TARGON_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {TARGON_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None, timeout: int | None = None) -> None:
    log("run " + " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, check=True)


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: Any) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def body_script(student: dict[str, str]) -> str:
    payload = read_json(ROOT / student["template_body"])
    script = payload["args"][0]
    template_model = student["template_model"]
    model = student["model"]
    if template_model not in script:
        raise RuntimeError(f"{template_model} not found in {student['template_body']}")
    script = script.replace(template_model, model)
    return script


def deploy_student(student: dict[str, str], cross_dir: Path, wait_timeout_s: int) -> str:
    targon = load_targon_module()
    cfg = targon.load_config(ROOT / ".env")
    project = targon.ensure_project(cfg)
    app = targon.ensure_app(cfg, project["uid"])
    volume = targon.ensure_volume(cfg)
    targon.wait_volume(cfg, volume["uid"], timeout_s=wait_timeout_s)
    ssh_key = targon.ensure_ssh_key(cfg)
    workload = targon.ensure_workload(
        cfg,
        project["uid"],
        app["uid"],
        volume["uid"],
        ssh_key["uid"],
        final=False,
    )
    body = {
        "image": targon.VLLM_IMAGE,
        "commands": ["/bin/bash", "-lc"],
        "args": [body_script(student)],
        "envs": targon.desired_envs(cfg),
        "ports": [{"port": PORT, "protocol": "TCP", "routing": "PROXIED"}],
        "volumes": [{"uid": volume["uid"], "mount_path": targon.HF_CACHE_PATH, "read_only": False}],
        "ssh_keys": [ssh_key["uid"]],
    }
    label = student["label"]
    log(f"{label} patch_workload model={student['model']}")
    targon.request_json(cfg, "PATCH", f"/workloads/{workload['uid']}", body)
    try:
        targon.deploy_workload(cfg, workload["uid"])
    except targon.TargonError as exc:
        if "HTTP 502" not in str(exc):
            raise
        log(f"{label} deploy returned transient 502; polling workload")
    state = targon.wait_workload(cfg, workload["uid"], timeout_s=wait_timeout_s)
    endpoint = targon.endpoint_from_state(state)
    if not endpoint:
        raise RuntimeError(f"{label} workload did not report endpoint")
    result = {
        "model": student["model"],
        "workload_uid": workload["uid"],
        "volume_uid": volume["uid"],
        "endpoint": f"{endpoint}/v1",
        "state": state,
    }
    write_json(cross_dir / label / "deploy_result.json", result)
    return f"{endpoint}/v1"


def stop_proxy_18088() -> None:
    ps = subprocess.run(
        ["ps", "-eo", "pid,args"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout
    pids = []
    for line in ps.splitlines():
        if "qwen_responses_namespace_proxy.py" in line and "--port 18088" in line:
            pids.append(line.strip().split(maxsplit=1)[0])
    if pids:
        log(f"stop_proxy_18088 pids={','.join(pids)}")
        subprocess.run(["kill", *pids], cwd=ROOT, check=False)
        time.sleep(2)


def start_proxy_18088(endpoint: str, log_path: Path) -> int:
    stop_proxy_18088()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [
            str(ROOT / ".venv/bin/python"),
            str(PROXY_SCRIPT),
            "--host",
            "127.0.0.1",
            "--port",
            "18088",
            "--upstream-base",
            endpoint,
            "--no-required-tool-choice",
        ],
        cwd=ROOT,
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log(f"proxy_18088_started pid={proc.pid} endpoint={endpoint} log={log_path}")
    time.sleep(2)
    return proc.pid


def run_provider_check(student: dict[str, str], out_dir: Path, timeout_s: int) -> bool:
    provider = out_dir / student["provider_file"]
    cmd = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "cubesandbox_swe.cli",
        "hint-eval",
        "provider-check",
        "--output",
        str(provider),
        "--scorer",
        "choice-logprobs",
        "--model",
        student["model"],
        "--base-url",
        LOCAL_BASE_URL,
        "--api-key-env",
        "no-auth",
        "--timeout",
        "180",
    ]
    deadline = time.monotonic() + timeout_s
    while True:
        proc = subprocess.run(cmd, cwd=ROOT)
        if proc.returncode == 0:
            return True
        if time.monotonic() >= deadline:
            return False
        log(f"{student['label']} provider not ready; retrying")
        time.sleep(30)


def score_student(student: dict[str, str], cross_dir: Path, concurrency: int) -> None:
    out_dir = cross_dir / student["label"]
    score = out_dir / student["score_file"]
    summary = out_dir / student["summary_file"]
    comparison = out_dir / "prefix_group_comparison.json"
    comparison_md = out_dir / "prefix_group_comparison.md"
    ablation = out_dir / student["ablation_file"]
    ablation_md = out_dir / student["ablation_md"]
    run(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "cubesandbox_swe.cli",
            "hint-eval",
            "score-batch",
            "--probes",
            str(out_dir / "probes.jsonl"),
            "--output",
            str(score),
            "--scorer",
            "choice-logprobs",
            "--model",
            student["model"],
            "--base-url",
            LOCAL_BASE_URL,
            "--api-key-env",
            "no-auth",
            "--timeout",
            "180",
            "--concurrency",
            str(concurrency),
            "--max-retries",
            "3",
            "--retry-backoff",
            "2",
            "--cache-dir",
            str(out_dir / "score_cache"),
            "--resume",
        ]
    )
    run(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "cubesandbox_swe.cli",
            "hint-eval",
            "analyze",
            "--scores",
            str(score),
            "--online-results-glob",
            str(out_dir / "online_results.jsonl"),
            "--output",
            str(summary),
        ]
    )
    run(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "cubesandbox_swe.cli",
            "hint-eval",
            "compare-prefix-groups",
            "--scores",
            str(score),
            "--online-results-glob",
            str(out_dir / "online_results.jsonl"),
            "--output",
            str(comparison),
            "--markdown",
            str(comparison_md),
            "--bootstrap-samples",
            "1000",
            "--seed",
            "0",
        ]
    )
    run(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "cubesandbox_swe.cli",
            "hint-eval",
            "ablate",
            "--scores",
            str(score),
            "--online-results-glob",
            str(out_dir / "online_results.jsonl"),
            "--output",
            str(ablation),
            "--markdown",
            str(ablation_md),
            "--bootstrap-samples",
            "1000",
            "--seed",
            "0",
        ]
    )


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def metric(summary: dict[str, Any], key: str) -> float | None:
    value = (summary.get("aggregate") or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return "n/a"
    return str(value)


def render_cross_report(cross_dir: Path) -> None:
    manifest = read_json(cross_dir / "teacher_success_manifest.json")
    lines = [
        "# Aliyun Teacher Cross-Check",
        "",
        "This run rebuilds the same hint-sensitivity experiment with the new Aliyun teacher trajectories, while keeping the three previously collected student online rollouts fixed. The old GPT-5.5 teacher results are shown as the reference teacher axis.",
        "",
        f"- Aliyun successful teacher trajectories: {manifest.get('success_count')}",
        "- Student online rollouts reused: 200 per model from the existing Qwen3.6, Affine, and Qwen3-32B experiments.",
        "- Shared settings: max 4 prefixes per trajectory, max 4 candidates, hint strength l2, seed 0.",
        "",
        "| Student | GPT-5.5 probes | Aliyun probes | Online success | GPT Goodness | Aliyun Goodness | Δ Goodness | GPT G_plus | Aliyun G_plus | GPT B | Aliyun B |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    rows = []
    for student in STUDENTS:
        label = student["label"]
        old = read_json(ROOT / student["old_summary"])
        new_path = cross_dir / label / student["summary_file"]
        if not new_path.exists():
            continue
        new = read_json(new_path)
        old_good = metric(old, "Goodness")
        new_good = metric(new, "Goodness")
        delta = None if old_good is None or new_good is None else new_good - old_good
        online = new.get("online") or {}
        online_success = "n/a"
        result_count = online.get("result_count")
        if isinstance(result_count, int) and result_count:
            old_online = read_json(cross_dir / label / "online_result_counts.json") if (cross_dir / label / "online_result_counts.json").exists() else {}
            ok = old_online.get("success_count")
            online_success = f"{ok}/{result_count}" if ok is not None else str(result_count)
        rows.append(
            [
                label,
                (old.get("aggregate") or {}).get("count"),
                (new.get("aggregate") or {}).get("count"),
                online_success,
                old_good,
                new_good,
                delta,
                metric(old, "G_plus"),
                metric(new, "G_plus"),
                metric(old, "B"),
                metric(new, "B"),
            ]
        )
    for row in rows:
        lines.append("| " + " | ".join(fmt(value) for value in row) + " |")
    lines.extend(
        [
            "",
            "## Interpretation Notes",
            "",
            "- This is a teacher-axis cross-check: student online capability is unchanged, while teacher-generated process prefixes and oracle targets change from GPT-5.5 to Aliyun.",
            "- Agreement in ordering or qualitative trend across both teacher axes would support the hypothesis that hint sensitivity is measuring model capability rather than idiosyncrasies of one teacher.",
            "- The Aliyun teacher sample is smaller because only verified successful teacher trajectories are used. Treat this run as robustness evidence, not as a replacement for the larger GPT-5.5 teacher run.",
            "",
        ]
    )
    for student in STUDENTS:
        label = student["label"]
        stats_path = cross_dir / label / (student["score_file"] + ".stats.json")
        if stats_path.exists():
            stats = read_json(stats_path)
            lines.append(f"## {label}")
            lines.append("")
            lines.append(f"- Score rows: {line_count(cross_dir / label / student['score_file'])}")
            lines.append(f"- Score-batch status: {stats.get('status')}")
            lines.append(f"- Errors: {len(stats.get('errors') or [])}")
            lines.append("")
    (cross_dir / "cross_teacher_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_online_counts(cross_dir: Path) -> None:
    for student in STUDENTS:
        label = student["label"]
        path = cross_dir / label / "online_results.jsonl"
        success = 0
        total = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            total += 1
            row = json.loads(line)
            score = row.get("score")
            if score is None and isinstance(row.get("verify"), dict):
                score = row["verify"].get("score")
            if isinstance(score, (int, float)) and float(score) > 0:
                success += 1
        write_json(cross_dir / label / "online_result_counts.json", {"total": total, "success_count": success})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cross-dir", type=Path, default=None)
    parser.add_argument("--wait-timeout-s", type=int, default=7200)
    parser.add_argument("--provider-timeout-s", type=int, default=2400)
    parser.add_argument("--score-concurrency", type=int, default=8)
    parser.add_argument("--student", action="append", choices=[s["label"] for s in STUDENTS])
    parser.add_argument("--skip-deploy", action="store_true")
    parser.add_argument("--skip-scored", action="store_true")
    args = parser.parse_args(argv)

    cross_dir = args.cross_dir or Path((ROOT / "results/hint_eval_full/latest_aliyun_teacher_cross_dir.txt").read_text().strip())
    selected = [s for s in STUDENTS if not args.student or s["label"] in set(args.student)]
    write_online_counts(cross_dir)
    status_path = cross_dir / "pipeline_status.json"
    status: dict[str, Any] = {"schema_version": "aliyun_teacher_cross_pipeline_status_v1", "cross_dir": str(cross_dir), "students": {}}
    for student in selected:
        label = student["label"]
        out_dir = cross_dir / label
        score_path = out_dir / student["score_file"]
        if args.skip_scored and score_path.exists() and line_count(score_path) == line_count(out_dir / "probes.jsonl"):
            log(f"{label} already scored; skipping")
            continue
        try:
            if not args.skip_deploy:
                endpoint = deploy_student(student, cross_dir, args.wait_timeout_s)
                start_proxy_18088(endpoint, cross_dir / "logs" / f"{label}.proxy_18088.log")
            if not run_provider_check(student, out_dir, args.provider_timeout_s):
                raise RuntimeError(f"{label} provider check did not pass")
            score_student(student, cross_dir, args.score_concurrency)
            status["students"][label] = {"status": "done", "score_rows": line_count(score_path)}
            write_json(status_path, status)
            render_cross_report(cross_dir)
        except Exception as exc:
            status["students"][label] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            write_json(status_path, status)
            raise
    render_cross_report(cross_dir)
    log("aliyun_teacher_cross_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
