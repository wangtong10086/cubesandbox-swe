"""Typed records for the hint-invariant evaluator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


HintCondition = Literal["neutral", "causal", "irrelevant", "misleading"]
CutpointType = Literal[
    "file_localization",
    "function_localization",
    "diagnosis",
    "edit_decision",
    "verification",
]

HINT_CONDITIONS: tuple[HintCondition, ...] = ("neutral", "causal", "irrelevant", "misleading")


@dataclass(frozen=True)
class CandidateAction:
    id: str
    kind: str
    label: str
    text: str
    command: str | None = None
    file_path: str | None = None
    operation: str | None = None
    is_positive: bool = False
    weight: float = 0.0
    source: str = "generic_distractor"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Cutpoint:
    action_index: int
    cutpoint_index: int
    cutpoint_type: CutpointType
    reason: str


@dataclass
class Probe:
    schema_version: str
    probe_id: str
    task_id: str | int | None
    instance_id: str | None
    repo: str | None
    trajectory_file: str
    attempt: int
    cutpoint_index: int
    cutpoint_type: str
    prefix_messages: list[dict[str, Any]]
    future_evidence_summary: str
    candidate_actions: list[dict[str, Any]]
    target_distribution: dict[str, float]
    hints: dict[str, str]
    leakage_flags: list[str]
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreRecord:
    schema_version: str
    probe_id: str
    task_id: str | int | None
    instance_id: str | None
    model: str
    scorer: str
    cutpoint_type: str
    target_distribution: dict[str, float]
    candidate_actions: list[dict[str, Any]]
    hints: dict[str, str]
    condition_scores: dict[str, dict[str, float]]
    trajectory_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def require_hint_conditions(hints: dict[str, str]) -> None:
    missing = [condition for condition in HINT_CONDITIONS if condition not in hints]
    if missing:
        raise ValueError(f"missing hint conditions: {', '.join(missing)}")


def normalize_distribution(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(weight)) for weight in weights.values())
    if total <= 0:
        raise ValueError("distribution has no positive mass")
    return {key: max(0.0, float(weight)) / total for key, weight in weights.items()}
