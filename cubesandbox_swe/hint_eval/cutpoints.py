"""Deterministic cutpoint selection heuristics."""

from __future__ import annotations

from .schemas import Cutpoint
from .trajectory import AttemptView, command_paths, is_inspect_command, is_source_path, is_test_or_verify_command


def select_cutpoints(attempt: AttemptView, *, max_cutpoints: int = 4) -> list[Cutpoint]:
    cutpoints: list[Cutpoint] = []
    add_file_localization(attempt, cutpoints)
    add_edit_decision(attempt, cutpoints)
    add_verification(attempt, cutpoints)

    unique: list[Cutpoint] = []
    seen: set[tuple[int, str]] = set()
    for cutpoint in sorted(cutpoints, key=lambda item: (item.action_index, item.cutpoint_type)):
        key = (cutpoint.action_index, cutpoint.cutpoint_type)
        if key not in seen:
            unique.append(Cutpoint(cutpoint.action_index, len(unique), cutpoint.cutpoint_type, cutpoint.reason))
            seen.add(key)
        if len(unique) >= max_cutpoints:
            break
    return unique


def add_file_localization(attempt: AttemptView, cutpoints: list[Cutpoint]) -> None:
    for action in attempt.actions:
        if action.tool == "cube_read_file" and is_source_path(action.path):
            cutpoints.append(
                Cutpoint(
                    action.index,
                    len(cutpoints),
                    "file_localization",
                    f"before first source file read: {action.path}",
                )
            )
            return
        if action.tool == "cube_run" and is_inspect_command(action.command):
            paths = [path for path in command_paths(action.command) if is_source_path(path)]
            if paths:
                cutpoints.append(
                    Cutpoint(
                        action.index,
                        len(cutpoints),
                        "file_localization",
                        f"before first inspect command mentioning source file: {paths[0]}",
                    )
                )
                return


def add_edit_decision(attempt: AttemptView, cutpoints: list[Cutpoint]) -> None:
    for action in attempt.actions:
        if action.tool == "cube_apply_patch":
            cutpoints.append(
                Cutpoint(action.index, len(cutpoints), "edit_decision", "before first patch application")
            )
            return


def add_verification(attempt: AttemptView, cutpoints: list[Cutpoint]) -> None:
    saw_patch = False
    for action in attempt.actions:
        if action.tool == "cube_apply_patch":
            saw_patch = True
            continue
        if not saw_patch:
            continue
        if action.tool == "cube_diff" or (action.tool == "cube_run" and is_test_or_verify_command(action.command)):
            cutpoints.append(
                Cutpoint(action.index, len(cutpoints), "verification", "after patch, before verification/diff action")
            )
            return
