"""Repository-relative paths used by the CubeSandbox SWE tools."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY_DIR = REPO_ROOT / "third_party"
CUBESANDBOX_DIR = THIRD_PARTY_DIR / "CubeSandbox"
AFFINETES_DIR = THIRD_PARTY_DIR / "affinetes"
CUBESANDBOX_SDK_PATH = CUBESANDBOX_DIR / "sdk" / "python"
SWE_INFINITE_PATH = AFFINETES_DIR / "environments" / "SWE-INFINITE"

DEFAULT_RESULTS_DIR = REPO_ROOT / "results"
DEFAULT_RUNS_DIR = REPO_ROOT / "swe-e2e-runs"
DEFAULT_ARTIFACTS_DIR = REPO_ROOT / "artifacts"
