# Contributing

## Development Setup

```sh
git submodule update --init --recursive
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Apply the patches under `patches/` when you need the local CubeSandbox or
affinetes changes in a fresh checkout.

## Checks

```sh
PYTHON_BIN=.venv/bin/python bash scripts/check.sh
```

Real CubeSandbox integration checks are intentionally opt-in because they need a
running local CubeSandbox installation and task templates.

Use `DOCTOR_ARGS="--runtime --runtime-smoke"` with `scripts/check.sh` when a
local CubeSandbox runtime is available. Use `cubesandbox-swe doctor --model` to
check the configured model endpoint without creating a sandbox.

## Artifact Rules

Do not commit full `results/`, `swe-e2e-runs/`, `.env`, logs, or machine-local
service configs. Use `cubesandbox-swe artifacts summarize` to update the source
repository manifest, and publish complete outputs as release or dataset assets.
