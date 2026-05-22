from __future__ import annotations

import json

from cubesandbox_swe.artifacts import build_manifest, write_manifest


def test_build_manifest_summarizes_local_trees(tmp_path) -> None:
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "one.json").write_text("{}", encoding="utf-8")

    manifest = build_manifest(tmp_path)

    assert manifest["schema_version"] == 1
    assert manifest["source_directories"]["results"]["files"] == 1
    assert manifest["source_directories"]["swe-e2e-runs"]["exists"] is False


def test_write_manifest(tmp_path) -> None:
    out_path = write_manifest(tmp_path / "artifacts" / "manifest.json", tmp_path)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["policy"] == "large artifacts are published outside the source repository"
