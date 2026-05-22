"""Hint generation and leakage checks."""

from __future__ import annotations

import re

from .schemas import CandidateAction


NEUTRAL_HINT = "Additional diagnostic note: No extra information is available."


def generate_hints(
    candidates: list[CandidateAction],
    *,
    cutpoint_type: str,
    hint_strength: str,
    final_patch: str,
) -> tuple[dict[str, str], list[str]]:
    positive = next((candidate for candidate in candidates if candidate.is_positive), None)
    negative = next((candidate for candidate in candidates if not candidate.is_positive), None)

    target = describe_candidate(positive) if positive else "the relevant source area"
    distractor = describe_candidate(negative) if negative else "project metadata"
    causal = causal_hint(target, cutpoint_type, hint_strength)
    irrelevant = "Additional diagnostic note: Project documentation may be useful context for this task."
    misleading = f"Additional diagnostic note: A plausible next step is to focus on {distractor}."
    hints = {
        "neutral": NEUTRAL_HINT,
        "causal": causal,
        "irrelevant": irrelevant,
        "misleading": misleading,
    }
    return hints, leakage_flags(hints, final_patch)


def describe_candidate(candidate: CandidateAction | None) -> str:
    if candidate is None:
        return "the relevant source area"
    if candidate.file_path:
        return candidate.file_path
    if candidate.operation:
        return candidate.operation.replace("_", " ")
    return candidate.label


def causal_hint(target: str, cutpoint_type: str, hint_strength: str) -> str:
    if hint_strength == "l1":
        return "Additional diagnostic note: Focus on the next action that narrows the failing behavior."
    if hint_strength == "l3":
        return f"Additional diagnostic note: The strongest next step is to use {target} for {cutpoint_type}."
    return f"Additional diagnostic note: The future successful path points to {target} for {cutpoint_type}."


def leakage_flags(hints: dict[str, str], final_patch: str) -> list[str]:
    flags: list[str] = []
    patch_chunks = suspicious_patch_chunks(final_patch)
    for condition, hint in hints.items():
        if "diff --git" in hint or "\n+++" in hint or "\n---" in hint or "@@" in hint:
            flags.append(f"{condition}:diff_marker")
        for chunk in patch_chunks:
            if chunk and chunk in hint:
                flags.append(f"{condition}:patch_substring")
                break
    return sorted(dict.fromkeys(flags))


def suspicious_patch_chunks(final_patch: str) -> list[str]:
    chunks: list[str] = []
    for line in final_patch.splitlines():
        if line.startswith(("+++", "---", "@@", "diff --git", "index ")):
            continue
        if not line.startswith(("+", "-")):
            continue
        text = re.sub(r"\s+", " ", line[1:].strip())
        if len(text) >= 24:
            chunks.append(text[:120])
        for quoted in re.findall(r"['\"]([^'\"]{24,})['\"]", text):
            chunks.append(quoted[:120])
    return chunks
