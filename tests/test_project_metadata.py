from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_metadata_has_open_source_basics() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]

    assert project["license"] == "MIT"
    assert project["readme"] == "README.md"
    assert "cubesandbox-swe" in project["scripts"]
    assert "Environment :: Console" in project["classifiers"]
    assert project["urls"]["Documentation"].endswith("/docs")


def test_ci_uses_shared_check_script() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "submodules: recursive" in ci
    assert "bash scripts/check.sh" in ci
