"""CLI for the offline hint-invariant evaluator."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import random
import time
from typing import Any

from .analysis import analyze_scores
from .candidates import generate_candidates
from .cutpoints import select_cutpoints
from .hints import generate_hints
from .io import expand_globs, read_json, read_jsonl, write_json, write_jsonl
from .metrics import kendall_tau, pearson, probe_metrics, spearman
from .report import write_report
from .schemas import Probe, require_hint_conditions
from .scoring import make_score_client, score_probe
from .trajectory import (
    AttemptView,
    TrajectoryView,
    load_trajectory,
    normalize_task_id_value,
    parse_task_id_from_text,
    prefix_messages,
)
from .v2 import build_onpolicy_probes, collect_prefixes, compare_prefix_groups, compute_support


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv or []))
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        raise
    if args.command == "build":
        return command_build(args)
    if args.command == "score":
        return command_score(args)
    if args.command == "score-batch":
        return command_score_batch(args)
    if args.command == "provider-check":
        return command_provider_check(args)
    if args.command == "analyze":
        return command_analyze(args)
    if args.command == "report":
        return command_report(args)
    if args.command == "collect-prefixes":
        return command_collect_prefixes(args)
    if args.command == "support":
        return command_support(args)
    if args.command == "build-onpolicy":
        return command_build_onpolicy(args)
    if args.command == "compare-prefix-groups":
        return command_compare_prefix_groups(args)
    if args.command == "ablate":
        return command_ablate(args)
    if args.command == "export" and args.export_command == "online-results":
        return command_export_online_results(args)
    parser.print_help()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cubesandbox-swe hint-eval")
    sub = parser.add_subparsers(dest="command")

    build = sub.add_parser("build", help="build hint probes from teacher trajectories")
    build.add_argument("--trajectory-glob", action="append", required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--max-cutpoints-per-trajectory", type=int, default=4)
    build.add_argument("--max-candidates", type=int, default=8)
    build.add_argument("--hint-strength", choices=["l1", "l2", "l3"], default="l2")
    build.add_argument("--seed", type=int, default=0)
    build.add_argument("--fail-on-leakage", action="store_true")
    build.set_defaults(command="build")

    score = sub.add_parser("score", help="score hint probes")
    score.add_argument("--probes", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--scorer", choices=["fake", "choice-logprobs"], required=True)
    score.add_argument("--model", required=True)
    score.add_argument("--base-url", default=None)
    score.add_argument("--api-key-env", default=None, help="API key env var; use 'no-auth' for endpoints without auth")
    score.add_argument("--timeout", type=float, default=60.0)
    score.set_defaults(command="score")

    score_batch = sub.add_parser("score-batch", help="score hint probes concurrently with cache/resume")
    score_batch.add_argument("--probes", type=Path, required=True)
    score_batch.add_argument("--output", type=Path, required=True)
    score_batch.add_argument("--scorer", choices=["fake", "choice-logprobs"], required=True)
    score_batch.add_argument("--model", required=True)
    score_batch.add_argument("--base-url", default=None)
    score_batch.add_argument("--api-key-env", default=None, help="API key env var; use 'no-auth' for endpoints without auth")
    score_batch.add_argument("--timeout", type=float, default=60.0)
    score_batch.add_argument("--concurrency", type=int, default=4)
    score_batch.add_argument("--max-retries", type=int, default=3)
    score_batch.add_argument("--retry-backoff", type=float, default=2.0)
    score_batch.add_argument("--cache-dir", type=Path, default=None)
    score_batch.add_argument("--resume", action="store_true")
    score_batch.set_defaults(command="score-batch")

    provider_check = sub.add_parser("provider-check", help="check scorer provider connectivity")
    provider_check.add_argument("--output", type=Path, required=True)
    provider_check.add_argument("--scorer", choices=["fake", "choice-logprobs"], required=True)
    provider_check.add_argument("--model", required=True)
    provider_check.add_argument("--base-url", default=None)
    provider_check.add_argument("--api-key-env", default=None, help="API key env var; use 'no-auth' for endpoints without auth")
    provider_check.add_argument("--timeout", type=float, default=60.0)
    provider_check.set_defaults(command="provider-check")

    analyze = sub.add_parser("analyze", help="aggregate scored probes")
    analyze.add_argument("--scores", type=Path, required=True)
    analyze.add_argument("--online-results-glob", action="append", default=[])
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--lambda", dest="lambda_", type=float, default=0.5)
    analyze.add_argument("--mu", type=float, default=0.25)
    analyze.add_argument("--nu", type=float, default=0.25)
    analyze.set_defaults(command="analyze")

    report_parser = sub.add_parser("report", help="generate markdown report")
    report_parser.add_argument("--summary", type=Path, required=True)
    report_parser.add_argument("--scores", type=Path, required=True)
    report_parser.add_argument("--output", type=Path, required=True)
    report_parser.set_defaults(command="report")

    collect = sub.add_parser("collect-prefixes", help="collect V2 teacher and student prefixes")
    collect.add_argument("--teacher-trajectory-glob", action="append", required=True)
    collect.add_argument("--student-trajectory-glob", action="append", required=True)
    collect.add_argument("--online-results-glob", action="append", default=[])
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--max-prefixes-per-trajectory", type=int, default=4)
    collect.add_argument("--seed", type=int, default=0)
    collect.set_defaults(command="collect-prefixes")

    support = sub.add_parser("support", help="compute V2 prefix support diagnostics")
    support.add_argument("--prefixes", type=Path, required=True)
    support.add_argument("--output", type=Path, required=True)
    support.add_argument("--student-model", required=True)
    support.set_defaults(command="support")

    build_onpolicy = sub.add_parser("build-onpolicy", help="build V2 on-policy-aware probes")
    build_onpolicy.add_argument("--prefixes", type=Path, required=True)
    build_onpolicy.add_argument("--support", type=Path, required=True)
    build_onpolicy.add_argument("--output", type=Path, required=True)
    build_onpolicy.add_argument("--max-candidates", type=int, default=4)
    build_onpolicy.add_argument("--hint-strength", choices=["l1", "l2", "l3"], default="l2")
    build_onpolicy.add_argument("--seed", type=int, default=0)
    build_onpolicy.set_defaults(command="build-onpolicy")

    compare = sub.add_parser("compare-prefix-groups", help="compare V2 metrics by prefix group")
    compare.add_argument("--scores", type=Path, required=True)
    compare.add_argument("--online-results-glob", action="append", default=[])
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--markdown", type=Path, default=None)
    compare.add_argument("--lambda", dest="lambda_", type=float, default=0.5)
    compare.add_argument("--mu", type=float, default=0.25)
    compare.add_argument("--nu", type=float, default=0.25)
    compare.add_argument("--bootstrap-samples", type=int, default=0)
    compare.add_argument("--seed", type=int, default=0)
    compare.set_defaults(command="compare-prefix-groups")

    ablate = sub.add_parser("ablate", help="compare Goodness against baseline offline metrics")
    ablate.add_argument("--scores", type=Path, required=True)
    ablate.add_argument("--online-results-glob", action="append", default=[])
    ablate.add_argument("--output", type=Path, required=True)
    ablate.add_argument("--markdown", type=Path, default=None)
    ablate.add_argument("--bootstrap-samples", type=int, default=1000)
    ablate.add_argument("--seed", type=int, default=0)
    ablate.add_argument("--lambda", dest="lambda_", type=float, default=0.5)
    ablate.add_argument("--mu", type=float, default=0.25)
    ablate.add_argument("--nu", type=float, default=0.25)
    ablate.set_defaults(command="ablate")

    export = sub.add_parser("export", help="export derived hint-eval artifacts")
    export_sub = export.add_subparsers(dest="export_command", required=True)
    online = export_sub.add_parser("online-results", help="export online verifier results as JSONL")
    online.add_argument("--input-glob", action="append", required=True)
    online.add_argument("--output", type=Path, required=True)
    export.set_defaults(command="export")
    return parser


def command_build(args: argparse.Namespace) -> int:
    trajectories = [load_trajectory(path) for path in expand_globs(args.trajectory_glob)]
    probes: list[dict[str, Any]] = []
    for trajectory in trajectories:
        probes.extend(
            build_probes_for_trajectory(
                trajectory,
                max_cutpoints=args.max_cutpoints_per_trajectory,
                max_candidates=args.max_candidates,
                hint_strength=args.hint_strength,
                seed=args.seed,
            )
        )
    leakage = [flag for probe in probes for flag in probe.get("leakage_flags", [])]
    if leakage and args.fail_on_leakage:
        raise SystemExit(f"leakage detected in generated hints: {', '.join(sorted(set(leakage)))}")
    write_jsonl(args.output, probes)
    counts = Counter(probe["cutpoint_type"] for probe in probes)
    print(f"wrote {len(probes)} probes to {args.output}")
    if counts:
        print("cutpoints: " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))
    return 0


def build_probes_for_trajectory(
    trajectory: TrajectoryView,
    *,
    max_cutpoints: int,
    max_candidates: int,
    hint_strength: str,
    seed: int,
) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    for attempt in trajectory.attempts:
        if not attempt.events and not attempt.patch:
            continue
        cutpoints = select_cutpoints(attempt, max_cutpoints=max_cutpoints)
        for cutpoint in cutpoints:
            candidates, target, evidence = generate_candidates(
                attempt,
                after_event_index=cutpoint.action_index,
                max_candidates=max_candidates,
            )
            if not candidates:
                continue
            hints, leakage_flags = generate_hints(
                candidates,
                cutpoint_type=cutpoint.cutpoint_type,
                hint_strength=hint_strength,
                final_patch=attempt.patch,
            )
            require_hint_conditions(hints)
            probe_id = make_probe_id(trajectory, attempt, cutpoint.cutpoint_index, seed)
            probe = Probe(
                schema_version="hint_eval_probe_v1",
                probe_id=probe_id,
                task_id=task_id(trajectory),
                instance_id=string_or_none(trajectory.task.get("instance_id")),
                repo=string_or_none(trajectory.task.get("repo")),
                trajectory_file=str(trajectory.path),
                attempt=attempt.attempt,
                cutpoint_index=cutpoint.cutpoint_index,
                cutpoint_type=cutpoint.cutpoint_type,
                prefix_messages=prefix_messages(attempt, cutpoint.action_index),
                future_evidence_summary=evidence,
                candidate_actions=[candidate.to_dict() for candidate in candidates],
                target_distribution=target,
                hints=hints,
                leakage_flags=leakage_flags,
                source={
                    "builder": "deterministic_v1",
                    "hint_strength": hint_strength,
                    "seed": seed,
                    "cutpoint_reason": cutpoint.reason,
                },
            )
            probes.append(probe.to_dict())
    return probes


def command_score(args: argparse.Namespace) -> int:
    probes = read_jsonl(args.probes)
    client = make_score_client(
        args.scorer,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        timeout=args.timeout,
    )
    records = [score_probe(probe, client=client, scorer=args.scorer, model=args.model).to_dict() for probe in probes]
    write_jsonl(args.output, records)
    print(f"wrote {len(records)} score records to {args.output}")
    return 0


def command_score_batch(args: argparse.Namespace) -> int:
    probes = read_jsonl(args.probes)
    existing = {str(record.get("probe_id")): record for record in read_jsonl(args.output)} if args.resume and args.output.exists() else {}
    cache_dir = args.cache_dir
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    client = make_score_client(
        args.scorer,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        timeout=args.timeout,
    )
    records: dict[str, dict[str, Any]] = {}
    for probe in probes:
        probe_id = str(probe.get("probe_id"))
        if probe_id in existing:
            records[probe_id] = refresh_score_record_metadata(probe, existing[probe_id])
    errors: list[dict[str, Any]] = []
    pending = [probe for probe in probes if str(probe.get("probe_id")) not in records]
    cache_hits = 0
    for probe in list(pending):
        cached = read_cached_score(cache_dir, probe, args) if cache_dir else None
        if cached:
            records[str(probe["probe_id"])] = refresh_score_record_metadata(probe, cached)
            pending.remove(probe)
            cache_hits += 1

    def worker(probe: dict[str, Any]) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        probe_id = str(probe["probe_id"])
        for attempt in range(args.max_retries + 1):
            try:
                record = score_probe(probe, client=client, scorer=args.scorer, model=args.model).to_dict()
                record = refresh_score_record_metadata(probe, record)
                write_cached_score(cache_dir, probe, args, record)
                return probe_id, record, None
            except Exception as exc:
                if attempt >= args.max_retries:
                    return probe_id, None, {"probe_id": probe_id, "error": f"{type(exc).__name__}: {exc}"}
                time.sleep(args.retry_backoff * (2 ** attempt))
        return probe_id, None, {"probe_id": probe_id, "error": "unreachable retry state"}

    with ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as pool:
        futures = {pool.submit(worker, probe): probe for probe in pending}
        for future in as_completed(futures):
            probe_id, record, error = future.result()
            if record is not None:
                records[probe_id] = record
            if error is not None:
                errors.append(error)

    ordered = [records[str(probe["probe_id"])] for probe in probes if str(probe.get("probe_id")) in records]
    write_jsonl(args.output, ordered)
    stats = {
        "schema_version": "hint_eval_score_batch_stats_v1",
        "probes": len(probes),
        "written": len(ordered),
        "existing_resume_records": len(existing),
        "cache_hits": cache_hits,
        "attempted": len(pending),
        "errors": errors,
        "status": "ok" if not errors and len(ordered) == len(probes) else "failed",
    }
    write_json(args.output.with_suffix(args.output.suffix + ".stats.json"), stats)
    print(f"wrote {len(ordered)} score records to {args.output}")
    if errors:
        print(f"score-batch failed records: {len(errors)}")
        return 1
    return 0


def refresh_score_record_metadata(probe: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Keep expensive scorer output while aligning metadata with the current probe."""
    refreshed = dict(record)
    for key in (
        "probe_id",
        "prefix_id",
        "task_id",
        "instance_id",
        "trajectory_file",
        "cutpoint_type",
        "prefix_source",
        "support_bucket",
        "prefix_group",
        "trajectory_resolved",
        "oracle_source",
        "candidate_actions",
        "target_distribution",
        "hints",
        "candidate_diagnostics",
    ):
        if key in probe:
            refreshed[key] = probe[key]
    return refreshed


def score_cache_path(cache_dir: Path | None, probe: dict[str, Any], args: argparse.Namespace) -> Path | None:
    if not cache_dir:
        return None
    key_payload = {
        "probe_id": probe.get("probe_id"),
        "model": args.model,
        "scorer": args.scorer,
        "target_distribution": probe.get("target_distribution"),
        "hints": probe.get("hints"),
        "candidate_actions": probe.get("candidate_actions"),
    }
    digest = hashlib.sha256(json.dumps(key_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


def read_cached_score(cache_dir: Path | None, probe: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    path = score_cache_path(cache_dir, probe, args)
    if not path or not path.exists():
        return None
    try:
        payload = read_json(path)
    except Exception:
        return None
    return payload if isinstance(payload, dict) and payload.get("probe_id") == probe.get("probe_id") else None


def write_cached_score(cache_dir: Path | None, probe: dict[str, Any], args: argparse.Namespace, record: dict[str, Any]) -> None:
    path = score_cache_path(cache_dir, probe, args)
    if path:
        write_json(path, record)


def command_provider_check(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {
        "schema_version": "hint_eval_provider_check_v1",
        "provider": args.scorer,
        "model": args.model,
        "base_url": args.base_url,
        "api_key_env": args.api_key_env,
        "status": "started",
    }
    try:
        client = make_score_client(
            args.scorer,
            model=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
        )
        score_probe(provider_check_probe(), client=client, scorer=args.scorer, model=args.model).to_dict()
    except Exception as exc:
        payload.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        write_json(args.output, payload)
        print(f"provider check failed: {payload['error']}")
        return 1

    payload.update({"status": "ok"})
    write_json(args.output, payload)
    print(f"provider check ok: {args.output}")
    return 0


def provider_check_probe() -> dict[str, Any]:
    return {
        "probe_id": "provider-check",
        "task_id": "provider-check",
        "instance_id": "provider-check",
        "trajectory_file": "",
        "cutpoint_type": "provider_check",
        "prefix_messages": [{"role": "user", "content": "Choose the best next SWE action."}],
        "candidate_actions": [
            {
                "id": "A",
                "kind": "operation",
                "label": "Inspect source",
                "text": "inspect the relevant source file",
                "operation": "inspect_file",
                "is_positive": True,
                "weight": 1.0,
                "source": "provider_check",
            },
            {
                "id": "B",
                "kind": "operation",
                "label": "Ignore task",
                "text": "ignore the issue and inspect unrelated documentation",
                "operation": "inspect_docs",
                "is_positive": False,
                "weight": 0.0,
                "source": "provider_check",
            },
        ],
        "target_distribution": {"A": 1.0},
        "hints": {
            "neutral": "No additional diagnostic note.",
            "causal": "Additional diagnostic note: inspect the source relevant to the issue.",
            "irrelevant": "Additional diagnostic note: unrelated docs may exist.",
            "misleading": "Additional diagnostic note: ignore the issue.",
        },
    }


def command_analyze(args: argparse.Namespace) -> int:
    summary = analyze_scores(
        args.scores,
        online_result_globs=args.online_results_glob,
        lambda_=args.lambda_,
        mu=args.mu,
        nu=args.nu,
    )
    write_json(args.output, summary)
    print(f"wrote summary to {args.output}")
    return 0


def command_report(args: argparse.Namespace) -> int:
    out = write_report(args.summary, args.scores, args.output)
    print(f"wrote report to {out}")
    return 0


def command_collect_prefixes(args: argparse.Namespace) -> int:
    records = collect_prefixes(
        teacher_globs=args.teacher_trajectory_glob,
        student_globs=args.student_trajectory_glob,
        online_globs=args.online_results_glob,
        output=args.output,
        max_prefixes_per_trajectory=args.max_prefixes_per_trajectory,
        seed=args.seed,
    )
    print(f"wrote {len(records)} prefixes to {args.output}")
    return 0


def command_support(args: argparse.Namespace) -> int:
    records = compute_support(args.prefixes, output=args.output, student_model=args.student_model)
    print(f"wrote {len(records)} support records to {args.output}")
    return 0


def command_build_onpolicy(args: argparse.Namespace) -> int:
    probes = build_onpolicy_probes(
        prefixes_path=args.prefixes,
        support_path=args.support,
        output=args.output,
        max_candidates=args.max_candidates,
        hint_strength=args.hint_strength,
        seed=args.seed,
    )
    print(f"wrote {len(probes)} on-policy probes to {args.output}")
    return 0


def command_compare_prefix_groups(args: argparse.Namespace) -> int:
    compare_prefix_groups(
        scores_path=args.scores,
        online_globs=args.online_results_glob,
        output=args.output,
        markdown=args.markdown,
        lambda_=args.lambda_,
        mu=args.mu,
        nu=args.nu,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    print(f"wrote prefix-group comparison to {args.output}")
    if args.markdown:
        print(f"wrote prefix-group markdown to {args.markdown}")
    return 0


def command_export_online_results(args: argparse.Namespace) -> int:
    rows = []
    for path in expand_globs(args.input_glob):
        try:
            payload = read_json(path)
        except Exception:
            continue
        record = online_result_record(payload, path)
        if record:
            rows.append(record)
    write_jsonl(args.output, rows)
    print(f"wrote {len(rows)} online result records to {args.output}")
    return 0


def online_result_record(payload: dict[str, Any], path: Path) -> dict[str, Any] | None:
    verify = verifier_payload(payload)
    score = verify.get("score") if isinstance(verify, dict) else None
    task_id = recover_task_id(payload, path)
    instance_id = recover_instance_id(payload)
    final = payload.get("final") if isinstance(payload.get("final"), dict) else {}
    status = payload.get("status") or payload.get("result_status") or final.get("status")
    warnings = []
    if task_id is None:
        warnings.append("missing_task_id")
    if instance_id is None:
        warnings.append("missing_instance_id")
    numeric_score = float(score) if isinstance(score, (int, float)) else None
    if numeric_score is None:
        warnings.append("missing_verify_score")
    return {
        "schema_version": "hint_eval_online_result_v1",
        "task_id": task_id,
        "instance_id": instance_id,
        "trajectory_model": payload.get("model") or payload.get("rollout_model") or payload.get("trajectory_model"),
        "score": numeric_score,
        "resolved": numeric_score > 0 if numeric_score is not None else False,
        "status": status,
        "verify_status": verify.get("status") if isinstance(verify, dict) else None,
        "source_file": str(path),
        "source_fields": ["verify.score"] if numeric_score is not None else [],
        "warnings": warnings,
    }


def verifier_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("verify"), dict):
        return payload["verify"]
    if isinstance(payload.get("verify_result"), dict):
        return payload["verify_result"]
    final = payload.get("final") if isinstance(payload.get("final"), dict) else {}
    if isinstance(final.get("verify"), dict):
        return final["verify"]
    attempts = payload.get("attempts") if isinstance(payload.get("attempts"), list) else []
    for attempt in reversed(attempts):
        if isinstance(attempt, dict) and isinstance(attempt.get("verify"), dict):
            return attempt["verify"]
    return {}


def recover_task_id(payload: dict[str, Any], path: Path) -> str | int | None:
    containers = [
        payload,
        payload.get("task") if isinstance(payload.get("task"), dict) else {},
        payload.get("extra") if isinstance(payload.get("extra"), dict) else {},
        payload.get("final") if isinstance(payload.get("final"), dict) else {},
    ]
    for container in containers:
        for key in ("task_id", "rollout_task_id"):
            value = normalize_task_id_value(container.get(key))
            if value is not None:
                return value
    for container in containers:
        for key in ("task_json", "trajectory_file", "result_file", "rollout_bucket_file"):
            value = container.get(key)
            if isinstance(value, str):
                parsed = parse_task_id_from_text(value)
                if parsed is not None:
                    return parsed
    return parse_task_id_from_text(str(path))


def recover_instance_id(payload: dict[str, Any]) -> str | None:
    for container in (
        payload,
        payload.get("task") if isinstance(payload.get("task"), dict) else {},
        payload.get("extra") if isinstance(payload.get("extra"), dict) else {},
    ):
        value = container.get("instance_id")
        if value is not None:
            return str(value)
    return None


def command_ablate(args: argparse.Namespace) -> int:
    score_records = read_jsonl(args.scores)
    rows = [probe_metrics(record, lambda_=args.lambda_, mu=args.mu, nu=args.nu) for record in score_records]
    online = analyze_scores(args.scores, online_result_globs=args.online_results_glob)["online"]
    online_scores = load_online_score_map(args.online_results_glob)
    joined = [{**row, "online_score": online_scores[key]} for row in rows if (key := online_join_key(row, online_scores)) is not None]
    baselines = baseline_rows(joined, seed=args.seed, bootstrap_samples=args.bootstrap_samples)
    payload = {
        "schema_version": "hint_eval_ablation_v1",
        "scores_file": str(args.scores),
        "joined_count": len(joined),
        "online": online,
        "baselines": baselines,
        "strongest_baseline": strongest_baseline(baselines),
        "bootstrap_samples": args.bootstrap_samples,
        "seed": args.seed,
    }
    write_json(args.output, payload)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_ablation_markdown(payload), encoding="utf-8")
    print(f"wrote ablation to {args.output}")
    if args.markdown:
        print(f"wrote ablation markdown to {args.markdown}")
    return 0


def load_online_score_map(patterns: list[str]) -> dict[str, float]:
    from .analysis import load_online_scores

    return load_online_scores(patterns)


def online_join_key(row: dict[str, Any], online_scores: dict[str, float]) -> str | None:
    for key in (row.get("instance_id"), row.get("task_id")):
        if key is not None and str(key) in online_scores:
            return str(key)
    return None


def baseline_rows(joined: list[dict[str, Any]], *, seed: int, bootstrap_samples: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    random_values = [rng.random() for _ in joined]
    candidates = {
        "Goodness=-B": [float(row["Goodness"]) for row in joined],
        "-L0": [-float(row["L0"]) for row in joined],
        "-L_plus": [-float(row["L_plus"]) for row in joined],
        "G_plus": [float(row["G_plus"]) for row in joined],
        "-S_irrelevant": [-float(row["S_irrelevant"]) for row in joined],
        "-H_misleading": [-float(row["H_misleading"]) for row in joined],
        "random": random_values,
    }
    online = [float(row["online_score"]) for row in joined]
    return [baseline_metric(name, values, online, bootstrap_samples=bootstrap_samples, seed=seed) for name, values in candidates.items()]


def baseline_metric(name: str, values: list[float], online: list[float], *, bootstrap_samples: int, seed: int) -> dict[str, Any]:
    return {
        "metric": name,
        "spearman": spearman(values, online),
        "kendall": kendall_tau(values, online),
        "pearson": pearson(values, online),
        "pairwise_accuracy": pairwise_accuracy_values(values, online),
        "bootstrap_ci": bootstrap_baseline_ci(values, online, samples=bootstrap_samples, seed=seed),
    }


def pairwise_accuracy_values(values: list[float], online: list[float]) -> float | None:
    if len(values) < 2 or len(values) != len(online):
        return None
    correct = 0
    total = 0
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            dv = (values[i] > values[j]) - (values[i] < values[j])
            do = (online[i] > online[j]) - (online[i] < online[j])
            if dv == 0 or do == 0:
                continue
            total += 1
            if dv == do:
                correct += 1
    return None if total == 0 else correct / total


def bootstrap_baseline_ci(values: list[float], online: list[float], *, samples: int, seed: int) -> dict[str, Any]:
    if samples <= 0 or len(values) < 2 or len(values) != len(online):
        return {"status": "not_run"}
    rng = random.Random(seed)
    n = len(values)
    buckets: dict[str, list[float]] = {"spearman": [], "kendall": [], "pearson": [], "pairwise_accuracy": []}
    for _ in range(samples):
        indexes = [rng.randrange(n) for _ in range(n)]
        xs = [values[index] for index in indexes]
        ys = [online[index] for index in indexes]
        metrics = {
            "spearman": spearman(xs, ys),
            "kendall": kendall_tau(xs, ys),
            "pearson": pearson(xs, ys),
            "pairwise_accuracy": pairwise_accuracy_values(xs, ys),
        }
        for key, value in metrics.items():
            if value is not None:
                buckets[key].append(float(value))
    return {key: percentile_interval(items) for key, items in buckets.items()}


def percentile_interval(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"status": "insufficient_data", "low": None, "high": None, "samples": 0}
    ordered = sorted(values)
    low_index = max(0, min(len(ordered) - 1, round(0.025 * (len(ordered) - 1))))
    high_index = max(0, min(len(ordered) - 1, round(0.975 * (len(ordered) - 1))))
    return {"status": "ok", "low": ordered[low_index], "high": ordered[high_index], "samples": len(ordered)}


def strongest_baseline(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in rows if row.get("metric") != "Goodness=-B" and row.get("spearman") is not None]
    if not candidates:
        return {"status": "insufficient_data"}
    return max(candidates, key=lambda row: float(row["spearman"]))


def render_ablation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Hint-Eval Ablation",
        "",
        f"- Scores: `{payload.get('scores_file')}`",
        f"- Joined probes: {payload.get('joined_count')}",
        "",
        "| Metric | Spearman | Kendall | Pearson | Pairwise |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in payload.get("baselines", []):
        lines.append(
            f"| {row.get('metric')} | {fmt_metric(row.get('spearman'))} | {fmt_metric(row.get('kendall'))} | "
            f"{fmt_metric(row.get('pearson'))} | {fmt_metric(row.get('pairwise_accuracy'))} |"
        )
    strongest = payload.get("strongest_baseline") or {}
    lines.extend(
        [
            "",
            f"Strongest non-Goodness baseline: `{strongest.get('metric', 'n/a')}`.",
            "",
            "`Goodness = -B`; higher is better.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def fmt_metric(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.6f}"


def make_probe_id(trajectory: TrajectoryView, attempt: AttemptView, cutpoint_index: int, seed: int) -> str:
    stem = trajectory.path.stem.replace(" ", "_")
    return f"{stem}:a{attempt.attempt}:c{cutpoint_index}:s{seed}"


def task_id(trajectory: TrajectoryView) -> str | int | None:
    for value in (trajectory.task.get("task_id"), trajectory.raw.get("task_id")):
        if value is not None:
            return value
    return None


def string_or_none(value: Any) -> str | None:
    return str(value) if value is not None and value != "" else None
