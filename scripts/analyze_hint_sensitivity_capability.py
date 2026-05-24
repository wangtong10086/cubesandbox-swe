#!/usr/bin/env python3
"""Generate the cross-model hint sensitivity capability report."""

from __future__ import annotations

import argparse
from pathlib import Path

from cubesandbox_swe.hint_eval.sensitivity_capability import analyze_default_runs, write_analysis_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/hint_eval_full/hint_sensitivity_capability_report.md"),
        help="Markdown report output path.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("results/hint_eval_full/hint_sensitivity_capability_analysis.json"),
        help="Machine-readable analysis output path.",
    )
    parser.add_argument("--raw-task-limit", type=int, default=30, help="Task rows to keep per model in the report.")
    parser.add_argument("--raw-probe-limit", type=int, default=30, help="Probe rows to keep per model in the report.")
    args = parser.parse_args()

    analysis = analyze_default_runs(raw_task_limit=args.raw_task_limit, raw_probe_limit=args.raw_probe_limit)
    markdown_path, json_path = write_analysis_outputs(analysis, markdown_output=args.output, json_output=args.json_output)
    print(f"wrote report to {markdown_path}")
    print(f"wrote analysis json to {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
