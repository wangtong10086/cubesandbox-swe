"""Artifact manifest generation for local SWE run outputs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from .paths import DEFAULT_ARTIFACTS_DIR, REPO_ROOT


def summarize_tree(path: Path) -> dict[str, Any]:
    files = 0
    bytes_total = 0
    if not path.exists():
        return {"exists": False, "files": 0, "bytes": 0}

    for root, _, filenames in os.walk(path):
        for filename in filenames:
            file_path = Path(root) / filename
            try:
                stat = file_path.stat()
            except OSError:
                continue
            files += 1
            bytes_total += stat.st_size
    return {"exists": True, "files": files, "bytes": bytes_total}


def build_manifest(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    results = summarize_tree(repo_root / "results")
    runs = summarize_tree(repo_root / "swe-e2e-runs")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": "large artifacts are published outside the source repository",
        "external_uri": None,
        "source_directories": {
            "results": results,
            "swe-e2e-runs": runs,
        },
        "tracked_examples": [
            "artifacts/examples/trajectory.example.json",
            "artifacts/examples/rollout_bucket.example.json",
        ],
        "notes": [
            "Do not commit full local run outputs to ordinary Git history.",
            "Attach complete results to a GitHub Release, object store, or dataset host.",
        ],
    }


def write_manifest(out_path: Path | None = None, repo_root: Path = REPO_ROOT) -> Path:
    out_path = out_path or (DEFAULT_ARTIFACTS_DIR / "manifest.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_manifest(repo_root)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path
