# cubesandbox-swe

`cubesandbox-swe` is a CubeSandbox integration layer for running, verifying,
and collecting SWE-INFINITE software-engineering tasks.

The repository is intentionally small: it contains the orchestration code,
documentation, and tests. CubeSandbox and affinetes are kept as pinned upstream
dependencies under `third_party/` and are expected to stay unmodified.

## What This Project Provides

- Prepare `affinefoundation/swe_infinite_images` Docker images as CubeSandbox templates.
- Run the affinetes SWE-INFINITE verifier inside CubeSandbox.
- Run Codex with CubeSandbox as the task runtime through MCP tools, without a
  host mirror of the task repository.
- Validate the sandbox lifecycle by pausing a prepared sandbox, reconnecting to
  it, and running the verifier after restore.
- Emit full trajectories and Affine rollout-bucket compatible records.
- Keep local run artifacts out of ordinary Git history while preserving schemas and manifests.

## Repository Layout

```text
cubesandbox_swe/          Python package and public CLI
scripts/                  Compatibility wrappers for older command paths
third_party/              Pinned, unmodified CubeSandbox and affinetes upstream checkouts
docs/                     Architecture, workflow, configuration, and artifact docs
artifacts/                Source-controlled schemas, manifests, and small examples
results/                  Local run output, ignored by Git
swe-e2e-runs/             Local sandbox workspaces, ignored by Git
```

## Quick Start

```sh
git submodule update --init --recursive
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Inspect commands without starting sandboxes:

```sh
cubesandbox-swe --help
cubesandbox-swe doctor
PYTHON_BIN=.venv/bin/python bash scripts/check.sh
cubesandbox-swe templates prepare --dry-run
cubesandbox-swe templates smoke --dry-run
cubesandbox-swe artifacts summarize
```

Start local CubeSandbox services and run a minimal template smoke test:

```sh
scripts/cubesandbox-wsl-start.sh
cubesandbox-swe doctor --runtime --runtime-smoke
cubesandbox-swe templates smoke --limit 1
```

Run a single solve/verify task once CubeSandbox is running locally:

```sh
cubesandbox-swe solve \
  --task-json results/swe_infinite_task_1.json \
  --solve-template swe-task1-rubocop-runner-v1 \
  --verify-template swe-task1-rubocop-runner-v1 \
  --codex-location sandbox
```

In the default `sandbox` runtime, the Codex CLI process keeps model access on
the host, while the task repository exists only inside CubeSandbox. Repository
reads, commands, patch application, and diff collection go through
CubeSandbox-backed MCP tools inside `/app`. The Codex binary and model API key
are not copied into the task sandbox, and there is no host-side task mirror.

To verify an existing patch and exercise the pause/reconnect lifecycle:

```sh
cubesandbox-swe verify \
  --task-json results/swe_infinite_task_1.json \
  --fix-patch results/task1_fix_patch.diff \
  --template-id swe-task1-rubocop-runner-v1
```

Successful verifier results include `state_after_save=paused` and
`state_after_restore=running`.

To validate model connectivity without starting a sandbox:

```sh
cubesandbox-swe doctor --model --model-preflight-timeout 60
```

The legacy script entrypoints still work and delegate to the CLI:

```sh
scripts/run_cubesandbox_codex_swe_e2e.py --help
scripts/run_affinetes_cubesandbox_swe_e2e.py --help
scripts/collect_cubesandbox_codex_swe50.py --help
```

## Documentation

- [Architecture](docs/architecture.md)
- [SWE-INFINITE Workflow](docs/swe-infinite-workflow.md)
- [Configuration](docs/configuration.md)
- [Hint-Invariant Evaluation](docs/hint-invariant-eval.md)
- [Hint-Invariant On-Policy Experiment](docs/hint-invariant-onpolicy-experiment.md)
- [Validation](docs/validation.md)
- [Artifacts](docs/artifacts.md)
- [Development](docs/development.md)
- [中文说明](docs/zh/README.md)

## Artifact Policy

Full local outputs under `results/` and `swe-e2e-runs/` can be many gigabytes.
They are not committed to ordinary Git history. Publish complete artifacts to a
GitHub Release, object store, or dataset host, then record the location in
`artifacts/manifest.json`.

## License

The integration code in this repository is licensed under MIT. CubeSandbox and
affinetes retain their upstream licenses in `third_party/`.
