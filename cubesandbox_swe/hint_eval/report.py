"""Markdown reports for hint-eval summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import read_json, read_jsonl


def generate_report(summary_path: str | Path, scores_path: str | Path) -> str:
    summary = read_json(summary_path)
    scores = read_jsonl(scores_path)
    aggregate = summary.get("aggregate", {})
    correlations = (summary.get("online") or {}).get("correlations") or {}
    lines = [
        "# Hint-Invariant SWE Offline Evaluation",
        "",
        "## Overview",
        "",
        f"- Trajectories loaded: {summary.get('trajectory_count', 0)}",
        f"- Probes scored: {summary.get('probe_count', 0)}",
        f"- Models: {', '.join(summary.get('models') or []) or 'unknown'}",
        f"- Scorers: {', '.join(summary.get('scorers') or []) or 'unknown'}",
        "",
        "## Aggregate Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key in ["L0", "G_plus", "S_irrelevant", "H_misleading", "B"]:
        lines.append(f"| `{key}` | {float(aggregate.get(key, 0.0)):.6f} |")

    lines.extend(
        [
            "",
            "## Probe Counts By Cutpoint",
            "",
            "| Cutpoint | Count | B |",
            "| --- | ---: | ---: |",
        ]
    )
    for row in summary.get("by_cutpoint_type", []):
        lines.append(f"| {row.get('cutpoint_type')} | {row.get('count', 0)} | {float(row.get('B', 0.0)):.6f} |")

    if any(row.get("prefix_group") not in {None, "null"} for row in summary.get("by_prefix_group", [])):
        lines.extend(
            [
                "",
                "## V2 Prefix Groups",
                "",
                "| Prefix group | Count | B | Goodness |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for row in summary.get("by_prefix_group", []):
            if row.get("prefix_group") in {None, "null"}:
                continue
            lines.append(
                f"| {row.get('prefix_group')} | {row.get('count', 0)} | "
                f"{float(row.get('B', 0.0)):.6f} | {float(row.get('Goodness', 0.0)):.6f} |"
            )

    lines.extend(["", "## Online Correlation", ""])
    if correlations.get("status") == "ok":
        lines.extend(
            [
                f"- Spearman: {format_optional(correlations.get('spearman'))}",
                f"- Kendall: {format_optional(correlations.get('kendall'))}",
                f"- Pairwise ranking accuracy: {format_optional(correlations.get('pairwise_ranking_accuracy'))}",
            ]
        )
    else:
        lines.append("- Correlation could not be computed because there were fewer than two joined online results.")

    lines.extend(["", "## Best And Worst Probes By B", ""])
    probes = sorted(summary.get("probes", []), key=lambda row: float(row.get("B", 0.0)))
    lines.extend(probe_table("Best", probes[:10]))
    lines.extend([""])
    lines.extend(probe_table("Worst", list(reversed(probes[-10:]))))

    lines.extend(["", "## Hint Examples", ""])
    example = scores[0] if scores else {}
    hints = example.get("hints") if isinstance(example.get("hints"), dict) else {}
    for condition in ("causal", "irrelevant", "misleading"):
        lines.extend([f"### {condition.title()}", "", hints.get(condition, "No example available."), ""])
    return "\n".join(lines).rstrip() + "\n"


def write_report(summary_path: str | Path, scores_path: str | Path, output: str | Path) -> Path:
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(generate_report(summary_path, scores_path), encoding="utf-8")
    return out


def format_optional(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.6f}"


def probe_table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [f"### {title}", "", "| Probe | Task | Cutpoint | B |", "| --- | --- | --- | ---: |"]
    if not rows:
        lines.append("| n/a | n/a | n/a | 0.000000 |")
    for row in rows:
        lines.append(
            f"| {row.get('probe_id')} | {row.get('instance_id') or row.get('task_id')} | "
            f"{row.get('cutpoint_type')} | {float(row.get('B', 0.0)):.6f} |"
        )
    return lines
