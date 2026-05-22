"""On-policy-aware hint-invariant experiment utilities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any

from .analysis import load_online_scores, online_key, online_score
from .candidates import GENERIC_DISTRACTORS, assign_ids, dedupe_candidates, file_candidate
from .cutpoints import select_cutpoints
from .hints import NEUTRAL_HINT, describe_candidate, leakage_flags
from .io import expand_globs, read_json, read_jsonl, write_json, write_jsonl
from .metrics import aggregate_metrics, kendall_tau, pearson, probe_metrics, spearman
from .schemas import CandidateAction, PrefixRecord, SupportRecord, normalize_distribution, require_hint_conditions
from .trajectory import (
    AttemptView,
    ToolAction,
    TrajectoryView,
    command_paths,
    derive_task_id,
    is_inspect_command,
    is_source_path,
    is_test_or_verify_command,
    load_trajectory,
    patch_touched_files,
    prefix_messages,
)


PREFIX_TEACHER = "teacher_success"
PREFIX_STUDENT = "student_onpolicy"
SUPPORT_BUCKETS = ("high", "medium", "low", "unknown")


@dataclass(frozen=True)
class OnlineResult:
    key: str
    score: float | None
    payload: dict[str, Any]

    @property
    def resolved(self) -> bool | None:
        if self.score is None:
            return None
        return self.score > 0


def collect_prefixes(
    *,
    teacher_globs: list[str],
    student_globs: list[str],
    online_globs: list[str],
    output: str | Path,
    max_prefixes_per_trajectory: int = 4,
    seed: int = 0,
) -> list[dict[str, Any]]:
    online_results = load_online_results(online_globs)
    records: list[dict[str, Any]] = []
    for path in expand_globs(teacher_globs):
        trajectory = load_trajectory(path)
        if trajectory_resolved(trajectory, online_results) is not True:
            continue
        records.extend(
            collect_trajectory_prefixes(
                trajectory,
                prefix_source=PREFIX_TEACHER,
                online_results=online_results,
                max_prefixes=max_prefixes_per_trajectory,
                seed=seed,
            )
        )
    for path in expand_globs(student_globs):
        trajectory = load_trajectory(path)
        records.extend(
            collect_trajectory_prefixes(
                trajectory,
                prefix_source=PREFIX_STUDENT,
                online_results=online_results,
                max_prefixes=max_prefixes_per_trajectory,
                seed=seed,
            )
        )
    write_jsonl(output, records)
    return records


def collect_trajectory_prefixes(
    trajectory: TrajectoryView,
    *,
    prefix_source: str,
    online_results: dict[str, OnlineResult],
    max_prefixes: int,
    seed: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    resolved = trajectory_resolved(trajectory, online_results)
    online = matching_online_result(trajectory, online_results)
    for attempt in trajectory.attempts:
        cutpoints = select_cutpoints(attempt, max_cutpoints=max_prefixes)
        for cutpoint in cutpoints:
            features = observed_state_features(attempt, before_event_index=cutpoint.action_index)
            future = future_actions(attempt, after_event_index=cutpoint.action_index)
            metadata = patch_metadata(trajectory, attempt)
            prefix_id = make_prefix_id(trajectory, prefix_source, attempt, cutpoint.cutpoint_index, seed)
            record = PrefixRecord(
                schema_version="hint_eval_prefix_v2",
                prefix_id=prefix_id,
                task_id=task_id(trajectory),
                instance_id=string_or_none(trajectory.task.get("instance_id")),
                repo=string_or_none(trajectory.task.get("repo")),
                prefix_source=prefix_source,
                trajectory_file=str(trajectory.path),
                trajectory_model=trajectory_model(trajectory),
                trajectory_resolved=resolved,
                cutpoint_type=cutpoint.cutpoint_type,
                cutpoint_index=cutpoint.cutpoint_index,
                attempt=attempt.attempt,
                prefix_messages=prefix_messages(attempt, cutpoint.action_index),
                observed_state_features=features,
                future_actions=future,
                patch_metadata=metadata,
                online_result=online.payload if online else {},
                source={
                    "builder": "onpolicy_v2",
                    "seed": seed,
                    "cutpoint_reason": cutpoint.reason,
                },
            )
            records.append(record.to_dict())
    return records


def compute_support(prefixes_path: str | Path, *, output: str | Path, student_model: str) -> list[dict[str, Any]]:
    prefixes = read_jsonl(prefixes_path)
    student_prefixes = [prefix for prefix in prefixes if prefix.get("prefix_source") == PREFIX_STUDENT]
    records: list[dict[str, Any]] = []
    for prefix in prefixes:
        if prefix.get("prefix_source") == PREFIX_STUDENT:
            record = SupportRecord(
                schema_version="hint_eval_support_v2",
                prefix_id=prefix["prefix_id"],
                prefix_source=PREFIX_STUDENT,
                student_model=student_model,
                support_bucket="high",
                state_feature_overlap=1.0,
                abstract_action_overlap=1.0,
                opened_gold_file=bool(prefix.get("observed_state_features", {}).get("opened_patch_touched_file")),
                ran_failing_test=bool(prefix.get("observed_state_features", {}).get("ran_test")),
                has_patch=bool(prefix.get("observed_state_features", {}).get("has_patch")),
                has_seen_error=bool(prefix.get("observed_state_features", {}).get("saw_error")),
                student_reached_similar_state=True,
                nearest_student_prefix_id=prefix["prefix_id"],
                source={"diagnostic": "student prefix is on policy by construction"},
            )
        else:
            record = support_for_teacher_prefix(prefix, student_prefixes, student_model)
        records.append(record.to_dict())
    write_jsonl(output, records)
    return records


def support_for_teacher_prefix(
    prefix: dict[str, Any],
    student_prefixes: list[dict[str, Any]],
    student_model: str,
) -> SupportRecord:
    same_task = [
        item
        for item in student_prefixes
        if task_match_key(item) is not None and task_match_key(item) == task_match_key(prefix)
    ]
    candidates = same_task or student_prefixes
    if not candidates:
        return SupportRecord(
            schema_version="hint_eval_support_v2",
            prefix_id=prefix["prefix_id"],
            prefix_source=PREFIX_TEACHER,
            student_model=student_model,
            support_bucket="unknown",
            state_feature_overlap=0.0,
            abstract_action_overlap=0.0,
            opened_gold_file=bool(prefix.get("observed_state_features", {}).get("opened_patch_touched_file")),
            ran_failing_test=bool(prefix.get("observed_state_features", {}).get("ran_test")),
            has_patch=bool(prefix.get("observed_state_features", {}).get("has_patch")),
            has_seen_error=bool(prefix.get("observed_state_features", {}).get("saw_error")),
            student_reached_similar_state=False,
            nearest_student_prefix_id=None,
            source={"diagnostic": "no student prefixes available"},
        )

    scored = [(support_overlap(prefix, student), student) for student in candidates]
    scored.sort(key=lambda item: (item[0][0], item[0][1]), reverse=True)
    (state_overlap, action_overlap), nearest = scored[0]
    combined = (state_overlap + action_overlap) / 2.0
    bucket = "high" if combined >= 0.5 else "medium" if combined >= 0.25 else "low"
    return SupportRecord(
        schema_version="hint_eval_support_v2",
        prefix_id=prefix["prefix_id"],
        prefix_source=PREFIX_TEACHER,
        student_model=student_model,
        support_bucket=bucket,
        state_feature_overlap=state_overlap,
        abstract_action_overlap=action_overlap,
        opened_gold_file=bool(prefix.get("observed_state_features", {}).get("opened_patch_touched_file")),
        ran_failing_test=bool(prefix.get("observed_state_features", {}).get("ran_test")),
        has_patch=bool(prefix.get("observed_state_features", {}).get("has_patch")),
        has_seen_error=bool(prefix.get("observed_state_features", {}).get("saw_error")),
        student_reached_similar_state=combined >= 0.5,
        nearest_student_prefix_id=nearest.get("prefix_id"),
        source={"diagnostic": "abstract SWE state feature overlap, not exact command matching"},
    )


def build_onpolicy_probes(
    *,
    prefixes_path: str | Path,
    support_path: str | Path,
    output: str | Path,
    max_candidates: int = 4,
    hint_strength: str = "l2",
    seed: int = 0,
) -> list[dict[str, Any]]:
    prefixes = read_jsonl(prefixes_path)
    support = {record["prefix_id"]: record for record in read_jsonl(support_path)}
    teacher_oracles = build_teacher_oracles(prefixes)
    probes: list[dict[str, Any]] = []
    for prefix in prefixes:
        support_record = support.get(prefix["prefix_id"], {})
        candidates, target, evidence, oracle_source = candidates_for_prefix(
            prefix,
            teacher_oracles=teacher_oracles,
            max_candidates=max_candidates,
        )
        if not candidates:
            continue
        hints, leakage = onpolicy_hints(
            candidates,
            cutpoint_type=str(prefix.get("cutpoint_type")),
            hint_strength=hint_strength,
            oracle_source=oracle_source,
            patch_text=str(prefix.get("patch_metadata", {}).get("patch") or ""),
        )
        require_hint_conditions(hints)
        support_bucket = str(support_record.get("support_bucket") or "unknown")
        probe = {
            "schema_version": "hint_eval_probe_v2",
            "probe_id": f"{prefix['prefix_id']}:probe:s{seed}",
            "prefix_id": prefix["prefix_id"],
            "task_id": prefix.get("task_id"),
            "instance_id": prefix.get("instance_id"),
            "repo": prefix.get("repo"),
            "trajectory_file": prefix.get("trajectory_file"),
            "attempt": prefix.get("attempt"),
            "cutpoint_index": prefix.get("cutpoint_index"),
            "cutpoint_type": prefix.get("cutpoint_type"),
            "prefix_source": prefix.get("prefix_source"),
            "support_bucket": support_bucket,
            "prefix_group": primary_prefix_group(prefix, support_bucket),
            "trajectory_resolved": prefix.get("trajectory_resolved"),
            "oracle_source": oracle_source,
            "prefix_messages": prefix.get("prefix_messages", []),
            "future_evidence_summary": evidence,
            "candidate_actions": [candidate.to_dict() for candidate in candidates],
            "target_distribution": target,
            "hints": hints,
            "leakage_flags": leakage,
            "prefix_support": support_record,
            "candidate_diagnostics": candidate_diagnostics(candidates, target),
            "source": {
                "builder": "onpolicy_v2",
                "hint_strength": hint_strength,
                "seed": seed,
                "oracle_source": oracle_source,
            },
        }
        probes.append(probe)
    write_jsonl(output, probes)
    return probes


def compare_prefix_groups(
    *,
    scores_path: str | Path,
    online_globs: list[str],
    output: str | Path,
    markdown: str | Path | None = None,
    lambda_: float = 0.5,
    mu: float = 0.25,
    nu: float = 0.25,
    bootstrap_samples: int = 0,
    seed: int = 0,
) -> dict[str, Any]:
    score_records = read_jsonl(scores_path)
    rows = [probe_metrics(record, lambda_=lambda_, mu=mu, nu=nu) | extra_score_metadata(record) for record in score_records]
    online_scores = load_online_scores(online_globs)
    group_rows = []
    for group in (
        "all_teacher_success_prefix",
        "high_support_teacher_prefix",
        "all_student_onpolicy_prefix",
        "student_success_prefix",
        "student_failure_prefix",
    ):
        members = [row for row in rows if row_in_group(row, group)]
        group_rows.append(group_summary(group, members, online_scores, bootstrap_samples=bootstrap_samples, seed=seed))
    comparison = {
        "schema_version": "hint_eval_prefix_group_comparison_v2",
        "scores_file": str(scores_path),
        "score_count": len(score_records),
        "prefix_source_counts": dict(Counter(str(row.get("prefix_source")) for row in rows)),
        "support_bucket_counts": dict(Counter(str(row.get("support_bucket") or "unknown") for row in rows)),
        "oracle_source_counts": dict(Counter(str(row.get("oracle_source") or "unknown") for row in rows)),
        "candidate_quality": candidate_quality(score_records),
        "groups": group_rows,
        "conclusion": conclusion_gate(score_records, group_rows),
        "baselines": baseline_summary(rows, online_scores),
    }
    write_json(output, comparison)
    if markdown:
        Path(markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(markdown).write_text(render_prefix_group_markdown(comparison), encoding="utf-8")
    return comparison


def load_online_results(patterns: list[str]) -> dict[str, OnlineResult]:
    out: dict[str, OnlineResult] = {}
    for path in expand_globs(patterns):
        try:
            payload = read_json(path)
        except Exception:
            continue
        key = online_key(payload)
        if key is None:
            continue
        out[key] = OnlineResult(key=key, score=online_score(payload), payload=payload)
    return out


def trajectory_resolved(trajectory: TrajectoryView, online_results: dict[str, OnlineResult]) -> bool | None:
    for container in (trajectory.raw.get("verify"), trajectory.raw.get("final"), trajectory.raw):
        if isinstance(container, dict):
            score = online_score(container)
            if score is not None:
                return score > 0
    online = matching_online_result(trajectory, online_results)
    if online and online.resolved is not None:
        return online.resolved
    return None


def matching_online_result(trajectory: TrajectoryView, online_results: dict[str, OnlineResult]) -> OnlineResult | None:
    for key in (trajectory.task.get("instance_id"), trajectory.task.get("task_id"), trajectory.raw.get("task_id")):
        if key is not None and str(key) in online_results:
            return online_results[str(key)]
    return None


def observed_state_features(attempt: AttemptView, *, before_event_index: int) -> dict[str, Any]:
    actions = [action for action in attempt.actions if action.index < before_event_index]
    patch_files = set(patch_touched_files(attempt.patch))
    opened_files: list[str] = []
    abstract_actions: list[str] = []
    saw_error = False
    has_patch = False
    ran_verification = False
    searched_symbol = False
    ran_test = False
    for action in actions:
        operation = abstract_operation(action)
        if operation:
            abstract_actions.append(operation)
        if action.tool == "cube_read_file" and action.path:
            opened_files.append(action.path)
        if action.tool == "cube_run":
            opened_files.extend(path for path in command_paths(action.command) if is_source_path(path) or is_test_file(path))
            searched_symbol = searched_symbol or operation == "search_symbol"
            ran_test = ran_test or operation == "run_test"
        if action.tool == "cube_apply_patch":
            has_patch = True
        if operation == "verify_patch":
            ran_verification = True
        if action.status == "failed" or has_error_text(action.result_text):
            saw_error = True
    opened_files = sorted(dict.fromkeys(opened_files))
    step_ratio = len(actions) / max(1, len(attempt.actions))
    return {
        "opened_files": opened_files,
        "opened_source_file": any(is_source_path(path) for path in opened_files),
        "opened_patch_touched_file": any(path in patch_files for path in opened_files),
        "opened_test_file": any(is_test_file(path) for path in opened_files),
        "searched_symbol": searched_symbol,
        "ran_test": ran_test,
        "saw_error": saw_error,
        "has_patch": has_patch,
        "ran_verification": ran_verification,
        "stopped": before_event_index >= len(attempt.events) - 1,
        "step_index_bucket": "early" if step_ratio < 0.34 else "mid" if step_ratio < 0.67 else "late",
        "abstract_actions": sorted(dict.fromkeys(abstract_actions)),
    }


def future_actions(attempt: AttemptView, *, after_event_index: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for action in attempt.actions:
        if action.index < after_event_index:
            continue
        operation = abstract_operation(action)
        files = []
        if action.path:
            files.append(action.path)
        if action.command:
            files.extend(command_paths(action.command))
        out.append(
            {
                "tool": action.tool,
                "operation": operation,
                "file_paths": sorted(dict.fromkeys(files)),
                "command": action.command[:240] if action.command else None,
                "status": action.status,
                "is_success": action.status != "failed",
            }
        )
    return out


def abstract_operation(action: ToolAction) -> str:
    if action.tool == "cube_read_file":
        return "inspect_file"
    if action.tool == "cube_apply_patch":
        return "edit_target_function"
    if action.tool == "cube_diff":
        return "verify_patch"
    if action.tool == "cube_run":
        lowered = action.command.lower()
        if is_test_or_verify_command(action.command):
            return "run_test" if "test" in lowered or "pytest" in lowered or "rspec" in lowered else "verify_patch"
        if any(token in lowered for token in ("grep", "rg")):
            return "search_symbol"
        if is_inspect_command(action.command):
            return "inspect_file"
    return "other"


def patch_metadata(trajectory: TrajectoryView, attempt: AttemptView) -> dict[str, Any]:
    patch = attempt.patch or str(trajectory.raw.get("fix_patch") or "")
    touched = patch_touched_files(patch)
    return {"patch_touched_files": touched, "patch": patch}


def support_overlap(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, float]:
    fa = feature_set(a.get("observed_state_features", {}))
    fb = feature_set(b.get("observed_state_features", {}))
    aa = set(a.get("observed_state_features", {}).get("abstract_actions") or [])
    ab = set(b.get("observed_state_features", {}).get("abstract_actions") or [])
    return jaccard(fa, fb), jaccard(aa, ab)


def feature_set(features: dict[str, Any]) -> set[str]:
    result = {
        key
        for key in (
            "opened_source_file",
            "opened_patch_touched_file",
            "opened_test_file",
            "searched_symbol",
            "ran_test",
            "saw_error",
            "has_patch",
            "ran_verification",
            "stopped",
        )
        if features.get(key)
    }
    bucket = features.get("step_index_bucket")
    if bucket:
        result.add(f"step:{bucket}")
    return result


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def candidates_for_prefix(
    prefix: dict[str, Any],
    *,
    teacher_oracles: dict[str, dict[str, Any]],
    max_candidates: int,
) -> tuple[list[CandidateAction], dict[str, float], str, str]:
    oracle = oracle_for_prefix(prefix, teacher_oracles)
    positives = positive_candidates_from_oracle(oracle)
    negatives = negative_candidates_from_prefix(prefix, positives)
    candidates = assign_ids(dedupe_candidates(positives + negatives)[:max_candidates])
    if not candidates or not any(candidate.is_positive for candidate in candidates):
        return [], {}, "", "unknown"
    target = normalize_distribution({candidate.id: candidate.weight for candidate in candidates if candidate.is_positive})
    evidence = summarize_oracle(oracle)
    return candidates, target, evidence, oracle["oracle_source"]


def build_teacher_oracles(prefixes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for prefix in prefixes:
        if prefix.get("prefix_source") != PREFIX_TEACHER:
            continue
        key = task_match_key(prefix)
        if key is None:
            continue
        files = list(prefix.get("patch_metadata", {}).get("patch_touched_files") or [])
        for action in prefix.get("future_actions", []):
            if isinstance(action, dict):
                files.extend(str(path) for path in action.get("file_paths") or [] if path)
        out.setdefault(key, {"files": [], "operations": [], "patch": "", "oracle_source": "teacher_future"})
        out[key]["files"].extend(files)
        out[key]["operations"].extend(
            str(action.get("operation"))
            for action in prefix.get("future_actions", [])
            if isinstance(action, dict) and action.get("operation")
        )
        if prefix.get("patch_metadata", {}).get("patch"):
            out[key]["patch"] = prefix["patch_metadata"]["patch"]
    for oracle in out.values():
        oracle["files"] = sorted(dict.fromkeys(path for path in oracle["files"] if path))
        oracle["operations"] = sorted(dict.fromkeys(op for op in oracle["operations"] if op and op != "other"))
    return out


def oracle_for_prefix(prefix: dict[str, Any], teacher_oracles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if prefix.get("prefix_source") == PREFIX_TEACHER:
        return {
            "files": list(prefix.get("patch_metadata", {}).get("patch_touched_files") or []),
            "operations": [
                str(action.get("operation"))
                for action in prefix.get("future_actions", [])
                if isinstance(action, dict) and action.get("operation") and action.get("operation") != "other"
            ],
            "patch": prefix.get("patch_metadata", {}).get("patch") or "",
            "oracle_source": "teacher_future",
        }
    if prefix.get("trajectory_resolved") is True:
        files = list(prefix.get("patch_metadata", {}).get("patch_touched_files") or [])
        return {
            "files": files,
            "operations": [
                str(action.get("operation"))
                for action in prefix.get("future_actions", [])
                if isinstance(action, dict) and action.get("operation") and action.get("operation") != "other"
            ],
            "patch": prefix.get("patch_metadata", {}).get("patch") or "",
            "oracle_source": "student_success_future",
        }
    key = task_match_key(prefix)
    if key and key in teacher_oracles:
        return teacher_oracles[key]
    return {
        "files": list(prefix.get("patch_metadata", {}).get("patch_touched_files") or []),
        "operations": ["inspect_relevant_file"],
        "patch": prefix.get("patch_metadata", {}).get("patch") or "",
        "oracle_source": "gold_patch",
    }


def positive_candidates_from_oracle(oracle: dict[str, Any]) -> list[CandidateAction]:
    candidates: list[CandidateAction] = []
    for path in oracle.get("files") or []:
        if path and path != "/dev/null":
            candidates.append(file_candidate(str(path), source=str(oracle.get("oracle_source") or "oracle")))
    for operation in oracle.get("operations") or []:
        if operation and operation not in {"inspect_file", "other"}:
            candidates.append(
                CandidateAction(
                    id="",
                    kind="operation",
                    label=str(operation).replace("_", " ").title(),
                    text=str(operation).replace("_", " "),
                    operation=str(operation),
                    is_positive=True,
                    weight=1.0,
                    source=str(oracle.get("oracle_source") or "oracle"),
                )
            )
    return candidates or [
        CandidateAction(
            id="",
            kind="operation",
            label="Inspect relevant source",
            text="inspect relevant source",
            operation="inspect_relevant_file",
            is_positive=True,
            weight=1.0,
            source=str(oracle.get("oracle_source") or "oracle"),
        )
    ]


def negative_candidates_from_prefix(prefix: dict[str, Any], positives: list[CandidateAction]) -> list[CandidateAction]:
    positive_files = {candidate.file_path for candidate in positives if candidate.file_path}
    negatives: list[CandidateAction] = []
    features = prefix.get("observed_state_features", {})
    for path in features.get("opened_files") or []:
        if path not in positive_files:
            negatives.append(
                CandidateAction(
                    id="",
                    kind="file",
                    label=f"Inspect {path}",
                    text=f"inspect {path}",
                    command=f"sed -n '1,200p' {path}",
                    file_path=path,
                    operation="inspect_file",
                    source="student_failed_action"
                    if prefix.get("prefix_source") == PREFIX_STUDENT and prefix.get("trajectory_resolved") is False
                    else "negative_distractor",
                )
            )
    negatives.extend(GENERIC_DISTRACTORS)
    return negatives


def onpolicy_hints(
    candidates: list[CandidateAction],
    *,
    cutpoint_type: str,
    hint_strength: str,
    oracle_source: str,
    patch_text: str,
) -> tuple[dict[str, str], list[str]]:
    positive = next((candidate for candidate in candidates if candidate.is_positive), None)
    negative = next((candidate for candidate in candidates if not candidate.is_positive), None)
    target = describe_candidate(positive)
    source_text = oracle_source.replace("_", " ")
    if hint_strength == "l1":
        causal = "Additional diagnostic note: Focus on the next action that narrows the failing behavior."
    elif hint_strength == "l3":
        causal = f"Additional diagnostic note: The {source_text} oracle points directly to {target}."
    else:
        causal = f"Additional diagnostic note: The {source_text} oracle points to {target} for {cutpoint_type}."
    distractor = describe_candidate(negative) if negative else "project metadata"
    hints = {
        "neutral": NEUTRAL_HINT,
        "causal": causal,
        "irrelevant": "Additional diagnostic note: Project documentation may be useful context for this task.",
        "misleading": f"Additional diagnostic note: A plausible next step is to focus on {distractor}.",
    }
    return hints, leakage_flags(hints, patch_text)


def candidate_diagnostics(candidates: list[CandidateAction], target: dict[str, float]) -> dict[str, Any]:
    positives = [candidate for candidate in candidates if candidate.is_positive]
    kinds = sorted({candidate.kind for candidate in candidates})
    entropy = -sum(prob * math.log(max(prob, 1e-12)) for prob in target.values())
    return {
        "positive_count": len(positives),
        "negative_count": len(candidates) - len(positives),
        "candidate_count": len(candidates),
        "candidate_kind": kinds[0] if len(kinds) == 1 else "mixed",
        "target_distribution_entropy": entropy,
    }


def primary_prefix_group(prefix: dict[str, Any], support_bucket: str) -> str:
    if prefix.get("prefix_source") == PREFIX_TEACHER and support_bucket == "high":
        return "high_support_teacher_prefix"
    if prefix.get("prefix_source") == PREFIX_TEACHER:
        return "all_teacher_success_prefix"
    if prefix.get("trajectory_resolved") is True:
        return "student_success_prefix"
    if prefix.get("trajectory_resolved") is False:
        return "student_failure_prefix"
    return "all_student_onpolicy_prefix"


def row_in_group(row: dict[str, Any], group: str) -> bool:
    if group == "all_teacher_success_prefix":
        return row.get("prefix_source") == PREFIX_TEACHER
    if group == "high_support_teacher_prefix":
        return row.get("prefix_source") == PREFIX_TEACHER and row.get("support_bucket") == "high"
    if group == "all_student_onpolicy_prefix":
        return row.get("prefix_source") == PREFIX_STUDENT
    if group == "student_success_prefix":
        return row.get("prefix_source") == PREFIX_STUDENT and row.get("trajectory_resolved") is True
    if group == "student_failure_prefix":
        return row.get("prefix_source") == PREFIX_STUDENT and row.get("trajectory_resolved") is False
    return False


def group_summary(
    group: str,
    rows: list[dict[str, Any]],
    online_scores: dict[str, float],
    *,
    bootstrap_samples: int = 0,
    seed: int = 0,
) -> dict[str, Any]:
    joined = join_group_online(rows, online_scores)
    goodness = [row["Goodness"] for row in joined]
    online = [row["online_score"] for row in joined]
    correlations = {
        "spearman_goodness_online": spearman(goodness, online),
        "kendall_goodness_online": kendall_tau(goodness, online),
        "pearson_goodness_online": pearson(goodness, online),
        "pairwise_accuracy": pairwise_accuracy(goodness, online),
        "bootstrap_ci": bootstrap_correlations(goodness, online, samples=bootstrap_samples, seed=seed),
        "joined_count": len(joined),
    }
    online_values = [item["online_score"] for item in joined]
    summary = aggregate_metrics(rows)
    return {
        "prefix_group": group,
        "probe_count": len(rows),
        "task_count": len({task_key(row) for row in rows if task_key(row) is not None}),
        **summary,
        "online_resolve_rate": (sum(1 for value in online_values if value > 0) / len(online_values)) if online_values else None,
        "correlations": correlations,
    }


def join_group_online(rows: list[dict[str, Any]], online_scores: dict[str, float]) -> list[dict[str, float]]:
    joined = []
    for row in rows:
        key = task_key(row)
        if key is not None and key in online_scores:
            joined.append({"Goodness": float(row["Goodness"]), "online_score": online_scores[key]})
    return joined


def pairwise_accuracy(goodness: list[float], online: list[float]) -> float | None:
    if len(goodness) < 2:
        return None
    correct = 0
    total = 0
    for i in range(len(goodness)):
        for j in range(i + 1, len(goodness)):
            dg = (goodness[i] > goodness[j]) - (goodness[i] < goodness[j])
            do = (online[i] > online[j]) - (online[i] < online[j])
            if dg == 0 or do == 0:
                continue
            total += 1
            if dg == do:
                correct += 1
    return None if total == 0 else correct / total


def bootstrap_correlations(goodness: list[float], online: list[float], *, samples: int, seed: int) -> dict[str, Any]:
    if samples <= 0 or len(goodness) < 2 or len(goodness) != len(online):
        return {"status": "not_run"}
    rng = random.Random(seed)
    values: dict[str, list[float]] = {"spearman": [], "kendall": [], "pearson": [], "pairwise_accuracy": []}
    n = len(goodness)
    for _ in range(samples):
        indexes = [rng.randrange(n) for _ in range(n)]
        g_sample = [goodness[index] for index in indexes]
        o_sample = [online[index] for index in indexes]
        metrics = {
            "spearman": spearman(g_sample, o_sample),
            "kendall": kendall_tau(g_sample, o_sample),
            "pearson": pearson(g_sample, o_sample),
            "pairwise_accuracy": pairwise_accuracy(g_sample, o_sample),
        }
        for key, value in metrics.items():
            if value is not None:
                values[key].append(float(value))
    return {key: percentile_ci(items) for key, items in values.items()}


def percentile_ci(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"status": "insufficient_data", "low": None, "high": None, "samples": 0}
    ordered = sorted(values)
    low = ordered[max(0, min(len(ordered) - 1, int(0.025 * (len(ordered) - 1))))]
    high = ordered[max(0, min(len(ordered) - 1, int(0.975 * (len(ordered) - 1))))]
    return {"status": "ok", "low": low, "high": high, "samples": len(ordered)}


def extra_score_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "prefix_id": record.get("prefix_id"),
        "prefix_source": record.get("prefix_source"),
        "support_bucket": record.get("support_bucket"),
        "prefix_group": record.get("prefix_group"),
        "oracle_source": record.get("oracle_source"),
        "trajectory_resolved": record.get("trajectory_resolved"),
        "candidate_diagnostics": record.get("candidate_diagnostics") or {},
    }


def candidate_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    diagnostics = [record.get("candidate_diagnostics") or {} for record in records]
    if not diagnostics:
        return {"count": 0}
    return {
        "count": len(diagnostics),
        "avg_candidate_count": sum(float(item.get("candidate_count") or 0) for item in diagnostics) / len(diagnostics),
        "avg_positive_count": sum(float(item.get("positive_count") or 0) for item in diagnostics) / len(diagnostics),
        "candidate_kind_counts": dict(Counter(str(item.get("candidate_kind") or "unknown") for item in diagnostics)),
    }


def baseline_summary(rows: list[dict[str, Any]], online_scores: dict[str, float]) -> dict[str, Any]:
    joined = [
        {**row, "online_score": online_scores[task_key(row)]}
        for row in rows
        if task_key(row) is not None and task_key(row) in online_scores
    ]
    if len(joined) < 2:
        return {"status": "insufficient_data"}
    online = [row["online_score"] for row in joined]
    return {
        "status": "ok",
        "goodness_spearman": spearman([row["Goodness"] for row in joined], online),
        "goodness_pearson": pearson([row["Goodness"] for row in joined], online),
        "negative_l0_spearman": spearman([-row["L0"] for row in joined], online),
        "negative_lplus_spearman": spearman([-row["L_plus"] for row in joined], online),
        "hint_gain_spearman": spearman([row["G_plus"] for row in joined], online),
    }


def conclusion_gate(records: list[dict[str, Any]], groups: list[dict[str, Any]]) -> dict[str, Any]:
    scorers = {str(record.get("scorer")) for record in records}
    models = {str(record.get("model")) for record in records}
    joined = sum(int(group.get("correlations", {}).get("joined_count") or 0) for group in groups)
    if not records or scorers == {"fake"} or models == {"fake-model"}:
        return {
            "level": 0,
            "strongest_supported_conclusion": "Pipeline works; no model capability claim.",
        }
    if joined == 0:
        return {"level": 1, "strongest_supported_conclusion": "Offline process sensitivity only."}
    if len(models) < 5:
        return {
            "level": 2,
            "strongest_supported_conclusion": "Task-level prediction and failure diagnosis for this model.",
        }
    return {
        "level": 3,
        "strongest_supported_conclusion": "Model-level ranking evidence under this scaffold.",
    }


def render_prefix_group_markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# Hint-Invariant On-Policy Experiment V2",
        "",
        "## 1. Experiment Setup",
        "",
        f"- Scores: `{comparison.get('scores_file')}`",
        f"- Score records: {comparison.get('score_count')}",
        "",
        "## 2. Prefix Source Counts",
        "",
        dict_table(comparison.get("prefix_source_counts") or {}),
        "",
        "## 3. Support Bucket Counts",
        "",
        dict_table(comparison.get("support_bucket_counts") or {}),
        "",
        "## 4. Hint Source Counts",
        "",
        dict_table(comparison.get("oracle_source_counts") or {}),
        "",
        "## 5. Candidate Quality Summary",
        "",
        dict_table(comparison.get("candidate_quality") or {}),
        "",
        "## 6. Offline Metrics By Prefix Group",
        "",
        "| Prefix group | Probes | Tasks | L0 | G_plus | S_irrelevant | H_misleading | B | Goodness | Online resolve rate | Spearman(Goodness, online) | Kendall | Pairwise |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group in comparison.get("groups", []):
        corr = group.get("correlations", {})
        lines.append(
            f"| {group.get('prefix_group')} | {group.get('probe_count')} | {group.get('task_count')} | "
            f"{fmt(group.get('L0'))} | {fmt(group.get('G_plus'))} | {fmt(group.get('S_irrelevant'))} | "
            f"{fmt(group.get('H_misleading'))} | {fmt(group.get('B'))} | {fmt(group.get('Goodness'))} | "
            f"{fmt(group.get('online_resolve_rate'))} | {fmt(corr.get('spearman_goodness_online'))} | "
            f"{fmt(corr.get('kendall_goodness_online'))} | {fmt(corr.get('pairwise_accuracy'))} |"
        )
    conclusion = comparison.get("conclusion") or {}
    lines.extend(
        [
            "",
            "## 7. Online Join Coverage",
            "",
            f"- Joined probe count: {sum(int(g.get('correlations', {}).get('joined_count') or 0) for g in comparison.get('groups', []))}",
            "",
            "## 8. Correlation With Online Outcome",
            "",
            "Correlations use `Goodness = -B`, so higher is better.",
            "",
            "## 9. Baseline Comparison",
            "",
            dict_table(comparison.get("baselines") or {}),
            "",
            "## 10. Failure-Mode Examples",
            "",
            "Inspect high `B` probes within each prefix group for failure diagnosis.",
            "",
            "## 11. Valid And Invalid Conclusions",
            "",
            f"- Conclusion level: {conclusion.get('level')}",
            f"- Strongest supported conclusion: {conclusion.get('strongest_supported_conclusion')}",
            "- Do not use all teacher prefixes as evidence of on-policy competence.",
            "- Primary claims should use student-on-policy and high-support teacher prefixes.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def dict_table(payload: dict[str, Any]) -> str:
    if not payload:
        return "| Key | Value |\n| --- | --- |\n| n/a | n/a |"
    lines = ["| Key | Value |", "| --- | --- |"]
    for key, value in sorted(payload.items()):
        lines.append(f"| {key} | {value} |")
    return "\n".join(lines)


def fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{float(value):.6f}"
    return str(value)


def is_test_file(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith("test/") or lowered.startswith("tests/") or "test_" in lowered or "_test." in lowered


def has_error_text(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("error", "failed", "failure", "traceback", "exception"))


def task_match_key(record: dict[str, Any]) -> str | None:
    for key in (record.get("instance_id"), record.get("task_id")):
        if key is not None:
            return str(key)
    return None


def task_key(row: dict[str, Any]) -> str | None:
    for key in (row.get("instance_id"), row.get("task_id")):
        if key is not None:
            return str(key)
    return None


def task_id(trajectory: TrajectoryView) -> str | int | None:
    return derive_task_id(trajectory)


def trajectory_model(trajectory: TrajectoryView) -> str | None:
    for container in (trajectory.environment, trajectory.raw.get("settings"), trajectory.raw):
        if isinstance(container, dict) and container.get("model"):
            return str(container["model"])
    return None


def string_or_none(value: Any) -> str | None:
    return str(value) if value is not None and value != "" else None


def make_prefix_id(trajectory: TrajectoryView, prefix_source: str, attempt: AttemptView, cutpoint_index: int, seed: int) -> str:
    stem = trajectory.path.stem.replace(" ", "_")
    identity = {
        "path": trajectory.path.as_posix(),
        "task_id": task_id(trajectory),
        "instance_id": string_or_none(trajectory.task.get("instance_id")),
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return f"{prefix_source}:{stem}:{digest}:a{attempt.attempt}:c{cutpoint_index}:s{seed}"


def summarize_oracle(oracle: dict[str, Any]) -> str:
    files = oracle.get("files") or []
    operations = oracle.get("operations") or []
    if files:
        return f"Oracle source {oracle.get('oracle_source')} points to {', '.join(files[:3])}."
    if operations:
        return f"Oracle source {oracle.get('oracle_source')} points to operation {operations[0]}."
    return f"Oracle source {oracle.get('oracle_source')} provides a relevant SWE action."
