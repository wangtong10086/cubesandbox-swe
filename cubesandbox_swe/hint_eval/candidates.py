"""Candidate action generation for hint probes."""

from __future__ import annotations

import string

from .schemas import CandidateAction, normalize_distribution
from .trajectory import (
    AttemptView,
    ToolAction,
    command_paths,
    is_inspect_command,
    is_source_path,
    is_test_or_verify_command,
    normalize_repo_path,
    patch_touched_files,
)


GENERIC_DISTRACTORS = [
    CandidateAction(
        id="",
        kind="file",
        label="Inspect project README",
        text="inspect README.md",
        command="sed -n '1,200p' README.md",
        file_path="README.md",
        operation="inspect_file",
        source="generic_distractor",
    ),
    CandidateAction(
        id="",
        kind="operation",
        label="Run unrelated full test suite",
        text="run full test suite",
        command="pytest -q",
        operation="run_full_test_suite",
        source="generic_distractor",
    ),
    CandidateAction(
        id="",
        kind="file",
        label="Inspect package configuration",
        text="inspect pyproject.toml",
        command="sed -n '1,200p' pyproject.toml",
        file_path="pyproject.toml",
        operation="inspect_config",
        source="generic_distractor",
    ),
    CandidateAction(
        id="",
        kind="operation",
        label="Edit documentation",
        text="edit docs before inspecting source",
        operation="edit_docs",
        source="generic_distractor",
    ),
]


def generate_candidates(
    attempt: AttemptView,
    *,
    after_event_index: int,
    max_candidates: int = 8,
) -> tuple[list[CandidateAction], dict[str, float], str]:
    future_actions = [action for action in attempt.actions if action.index >= after_event_index]
    positives = positive_candidates(future_actions, attempt.patch)
    if not positives:
        return [], {}, "no positive future actions found"
    negatives = negative_candidates(attempt, positives)

    selected = dedupe_candidates(positives + negatives)[:max_candidates]
    selected = assign_ids(selected)
    weights = {candidate.id: candidate.weight for candidate in selected if candidate.is_positive}
    target = normalize_distribution(weights)
    summary = summarize_evidence([candidate for candidate in selected if candidate.is_positive])
    return selected, target, summary


def positive_candidates(actions: list[ToolAction], final_patch: str) -> list[CandidateAction]:
    candidates: list[CandidateAction] = []
    for action in actions:
        if action.tool == "cube_read_file" and action.path:
            candidates.append(file_candidate(action.path, source="future_success_action"))
        elif action.tool == "cube_run":
            candidates.extend(command_candidates(action.command))
        elif action.tool == "cube_apply_patch":
            files = patch_touched_files(action.patch or final_patch)
            label = f"Edit {', '.join(files[:2])}" if files else "Edit target source"
            candidates.append(
                CandidateAction(
                    id="",
                    kind="operation",
                    label=label,
                    text=label.lower(),
                    operation="edit_target_function",
                    is_positive=True,
                    weight=1.0,
                    source="future_success_action",
                )
            )
    for path in patch_touched_files(final_patch):
        candidates.append(file_candidate(path, source="gold_patch_metadata"))
    return candidates


def file_candidate(path: str, *, source: str) -> CandidateAction:
    clean = normalize_repo_path(path)
    return CandidateAction(
        id="",
        kind="file",
        label=f"Inspect {clean}",
        text=f"inspect {clean}",
        command=f"sed -n '1,200p' {clean}",
        file_path=clean,
        operation="inspect_file",
        is_positive=True,
        weight=1.0,
        source=source,
    )


def command_candidates(command: str) -> list[CandidateAction]:
    if not command:
        return []
    lowered = command.lower()
    candidates: list[CandidateAction] = []
    if is_inspect_command(command):
        for path in command_paths(command):
            if is_source_path(path):
                candidates.append(file_candidate(path, source="future_success_action"))
        operation = "search_symbol" if any(token in lowered for token in ("grep", "rg")) else "inspect_relevant_file"
        candidates.append(
            CandidateAction(
                id="",
                kind="command",
                label="Run future inspect command",
                text=command[:240],
                command=command,
                operation=operation,
                is_positive=True,
                weight=0.75,
                source="future_success_action",
            )
        )
    elif is_test_or_verify_command(command):
        candidates.append(
            CandidateAction(
                id="",
                kind="operation",
                label="Verify patch",
                text="verify the patch with the relevant test or diff command",
                command=command,
                operation="verify_patch",
                is_positive=True,
                weight=1.0,
                source="future_success_action",
            )
        )
    return candidates


def negative_candidates(attempt: AttemptView, positives: list[CandidateAction]) -> list[CandidateAction]:
    positive_files = {candidate.file_path for candidate in positives if candidate.file_path}
    observed_files: list[str] = []
    for action in attempt.actions:
        if action.path:
            observed_files.append(action.path)
        if action.command:
            observed_files.extend(command_paths(action.command))
    negatives: list[CandidateAction] = []
    for path in sorted(dict.fromkeys(observed_files)):
        if path not in positive_files and path and not path.startswith(".git/"):
            negatives.append(
                CandidateAction(
                    id="",
                    kind="file",
                    label=f"Inspect {path}",
                    text=f"inspect {path}",
                    command=f"sed -n '1,200p' {path}",
                    file_path=path,
                    operation="inspect_file",
                    source="negative_distractor",
                )
            )
    negatives.extend(GENERIC_DISTRACTORS)
    return negatives


def dedupe_candidates(candidates: list[CandidateAction]) -> list[CandidateAction]:
    deduped: list[CandidateAction] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        key = (candidate.kind, candidate.file_path or "", candidate.operation or candidate.text)
        if key in seen:
            continue
        deduped.append(candidate)
        seen.add(key)
    return deduped


def assign_ids(candidates: list[CandidateAction]) -> list[CandidateAction]:
    assigned: list[CandidateAction] = []
    for label, candidate in zip(string.ascii_uppercase, candidates):
        assigned.append(
            CandidateAction(
                id=label,
                kind=candidate.kind,
                label=candidate.label,
                text=candidate.text,
                command=candidate.command,
                file_path=candidate.file_path,
                operation=candidate.operation,
                is_positive=candidate.is_positive,
                weight=candidate.weight,
                source=candidate.source,
            )
        )
    return assigned


def summarize_evidence(positives: list[CandidateAction]) -> str:
    files = [candidate.file_path for candidate in positives if candidate.file_path]
    operations = [candidate.operation for candidate in positives if candidate.operation and not candidate.file_path]
    if files:
        return f"Future successful trajectory focuses on {', '.join(dict.fromkeys(files[:3]))}."
    if operations:
        return f"Future successful trajectory uses operation {operations[0]}."
    return "Future successful trajectory contains a relevant inspect/edit/verify action."
