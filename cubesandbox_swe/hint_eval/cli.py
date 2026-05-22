"""CLI for the offline hint-invariant evaluator."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .analysis import analyze_scores
from .candidates import generate_candidates
from .cutpoints import select_cutpoints
from .hints import generate_hints
from .io import expand_globs, read_jsonl, write_json, write_jsonl
from .report import write_report
from .schemas import Probe, require_hint_conditions
from .scoring import make_score_client, score_probe
from .trajectory import AttemptView, TrajectoryView, load_trajectory, prefix_messages


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
    if args.command == "analyze":
        return command_analyze(args)
    if args.command == "report":
        return command_report(args)
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
    score.add_argument("--api-key-env", default=None)
    score.add_argument("--timeout", type=float, default=60.0)
    score.set_defaults(command="score")

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
