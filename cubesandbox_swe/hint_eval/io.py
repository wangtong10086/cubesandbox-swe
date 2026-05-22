"""JSON and JSONL utilities for hint-eval artifacts."""

from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any, Iterable


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: Any) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL record at {path}:{line_no} is not an object")
        records.append(value)
    return records


def write_jsonl(path: str | Path, records: Iterable[dict[str, Any]]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return out


def expand_globs(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = [Path(path) for path in glob.glob(pattern, recursive=True)]
        if not matches and Path(pattern).exists():
            matches = [Path(pattern)]
        paths.extend(path for path in matches if path.is_file())
    return sorted(dict.fromkeys(paths), key=lambda path: str(path))
