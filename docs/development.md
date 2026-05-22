# Development

## Bootstrap

```sh
git submodule update --init --recursive
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Dependency Policy

Do not keep local changes in `third_party/CubeSandbox` or `third_party/affinetes`.
The normal development state is:

```sh
git -C third_party/CubeSandbox status --short
git -C third_party/affinetes status --short
```

Both commands should print nothing. If an upstream bug is found, submit it
upstream or pin to a fixed upstream revision; keep compatibility wrappers in
`cubesandbox_swe/`.

Do not add project instructions that require applying patches inside
`third_party/`. The supported path is direct use of the pinned upstream
checkouts.

## Tests

```sh
PYTHON_BIN=.venv/bin/python bash scripts/check.sh
```

Set `SKIP_DOCTOR=1` when running the package checks before initializing
submodules. Set `DOCTOR_ARGS="--runtime --runtime-smoke"` to include a local
CubeSandbox smoke test.

Real CubeSandbox tests are opt-in because they require local services and
templates. Unit and mocked integration tests should not start sandboxes.

For a local runtime smoke test, start services and run one ready template:

```sh
scripts/cubesandbox-wsl-start.sh
cubesandbox-swe doctor --runtime --runtime-smoke
cubesandbox-swe templates smoke --limit 1
```

For end-to-end runtime validation, use the command ladder in
[Validation](validation.md). The minimum acceptance bar for changes touching
runtime behavior is:

- `scripts/check.sh` passes.
- The two `third_party` status commands print nothing.
- `cubesandbox-swe doctor --runtime --codex-runtime-smoke` passes.
- A verifier run records `state_after_save=paused` and
  `state_after_restore=running`.

## Review Checklist

Before opening a change for review, scan for old runtime assumptions:

```sh
python - <<'PY'
from __future__ import annotations

from pathlib import Path

patterns = [
    "/mnt/" + "swe",
    "host-" + "mount",
    "save" + "_state",
    "restore" + "_state",
    "CODEX" + "_AGENT_PATH",
    "agents/" + "codex",
    "workspace-" + "write",
    "patches/" + "cubesandbox",
    "patches/" + "affinetes",
]
roots = ["cubesandbox_swe", "tests", "README.md", "docs", "scripts", ".env.example", "pyproject.toml"]
hits = []
for root in roots:
    path = Path(root)
    files = [path] if path.is_file() else [
        p
        for p in path.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    for file_path in files:
        text = file_path.read_text(errors="ignore")
        for pattern in patterns:
            if pattern in text:
                hits.append((str(file_path), pattern))
if hits:
    for file_path, pattern in hits:
        print(f"{file_path}: {pattern}")
    raise SystemExit(1)
print("NO_LEGACY_RUNTIME_REFERENCES")
PY
```

The command should print `NO_LEGACY_RUNTIME_REFERENCES`.
