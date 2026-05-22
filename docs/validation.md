# Validation

Use this page to prove that the project works with pinned upstream CubeSandbox
and affinetes checkouts, without dependency patches or a host-side task mirror.

## 1. Static Checks

Run the project quality gate:

```sh
PYTHON_BIN=.venv/bin/python bash scripts/check.sh
```

Confirm that upstream dependencies are clean:

```sh
git -C third_party/CubeSandbox status --short
git -C third_party/affinetes status --short
```

Both commands should print nothing.

## 2. Runtime Doctor

Start local CubeSandbox services, then verify the local runtime path:

```sh
scripts/cubesandbox-wsl-start.sh
cubesandbox-swe doctor --runtime --codex-runtime-smoke \
  --json results/upstream-cubesandbox-validation/doctor-runtime.json
```

Expected checks include CubeSandbox CLI, CubeSandbox API health, the snapshot
capability flag, READY templates, and the Codex runtime smoke command inside
`/app`.

## 3. Model Doctor

Verify the Codex CLI and model endpoint without starting a task sandbox:

```sh
cubesandbox-swe doctor --model --model-preflight-timeout 180 \
  --json results/upstream-cubesandbox-validation/doctor-model.json
```

The result should report `model preflight` as `ok`.

## 4. Verifier Lifecycle

Verify a known patch and require the pause/reconnect lifecycle to succeed:

```sh
cubesandbox-swe verify \
  --task-json results/swe_infinite_task_1.json \
  --fix-patch results/task1_fix_patch.diff \
  --template-id swe-task1-rubocop-runner-v1 \
  --verify-timeout 1800
```

Successful result files must include:

```text
status=ok
score=1.0
state_after_save=paused
state_after_restore=running
```

This path uses upstream `sandbox.pause()` and `Sandbox.connect(sandbox_id)`.
It does not require a CubeSandbox SDK patch.

## 5. End-to-End Solve

Run one real SWE task with Codex using CubeSandbox as the task runtime:

```sh
cubesandbox-swe solve \
  --task-json results/swe50_trajectories/tasks/task_00000010955.json \
  --solve-template swe-jongracecox-anybadge-efab2f6f-58-e9a1d593 \
  --verify-template swe-jongracecox-anybadge-efab2f6f-58-e9a1d593 \
  --codex-location sandbox \
  --max-verify-attempts 1 \
  --solve-timeout 900 \
  --verify-timeout 1800 \
  --output-dir results/upstream-cubesandbox-validation \
  --runs-dir swe-e2e-runs/upstream-cubesandbox-validation
```

Expected successful fields:

```text
status=ok
runtime=cubesandbox-mcp
codex_location=sandbox
verify.score=1.0
verify.state_after_save=paused
verify.state_after_restore=running
```

The task repository should be accessed only through CubeSandbox MCP tools under
`/app`. The Codex binary and model API key should stay outside the task sandbox.

## 6. Secret Scan

Before publishing artifacts, scan active code, docs, and selected validation
outputs for exact sensitive `.env` values. The scanner should print key counts
and paths only, not secret values.

```sh
python - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

root = Path.cwd()
env_path = root / ".env"
markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "CREDENTIAL", "AUTH")
values: dict[str, str] = {}

if env_path.exists():
    for raw in env_path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if value and len(value) >= 8 and any(marker in key.upper() for marker in markers):
            values[key] = value

scan_roots = [
    root / "cubesandbox_swe",
    root / "tests",
    root / "docs",
    root / "scripts",
    root / "README.md",
    root / ".env.example",
    root / "results" / "upstream-cubesandbox-validation",
]

skip_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "third_party"}
files: list[Path] = []
for scan_root in scan_roots:
    if scan_root.is_file():
        files.append(scan_root)
    elif scan_root.exists():
        for dirpath, dirnames, filenames in os.walk(scan_root):
            dirnames[:] = [name for name in dirnames if name not in skip_dirs]
            for filename in filenames:
                path = Path(dirpath) / filename
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= 10_000_000:
                    files.append(path)

hits: list[tuple[str, str]] = []
for path in dict.fromkeys(files):
    text = path.read_text(errors="ignore")
    for key, value in values.items():
        if value in text:
            hits.append((key, str(path.relative_to(root))))

print(f"SENSITIVE_ENV_KEYS_SCANNED={len(values)}")
print(f"FILES_SCANNED={len(files)}")
if hits:
    print("LEAK_HITS")
    for key, path in hits:
        print(f"{key}\t{path}")
    raise SystemExit(1)
print("NO_EXACT_ENV_VALUE_LEAKS")
PY
```

## Latest Local Validation

The latest local validation summary is written to:

```text
results/upstream-cubesandbox-validation/validation-summary.md
```

That file records the exact commands, result files, and observed verifier
state for the current workstation. It is a local run artifact, not a substitute
for rerunning validation after runtime changes.
