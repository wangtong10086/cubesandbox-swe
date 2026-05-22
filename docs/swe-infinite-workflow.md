# SWE-INFINITE Workflow

This workflow assumes CubeSandbox and affinetes remain unmodified under
`third_party/`. Local compatibility logic lives in `cubesandbox_swe/`.

## 1. Prepare Templates

```sh
cubesandbox-swe doctor
cubesandbox-swe templates prepare --dry-run
cubesandbox-swe templates prepare
```

The preparation flow reads `results/swe_infinite_images_50_results.json`,
creates deterministic template ids, and writes local state to
`results/cubesandbox_swe_templates.json`.

## 2. Smoke Test Templates

```sh
scripts/cubesandbox-wsl-start.sh
cubesandbox-swe doctor --runtime --runtime-smoke
cubesandbox-swe templates smoke --dry-run
cubesandbox-swe templates smoke --limit 1
cubesandbox-swe templates smoke
```

Smoke tests create a sandbox from each ready template through the CubeSandbox
SDK, run a small command through `cubecli exec`, then destroy the sandbox. That
matches the execution path used by the SWE verifier. Use `--limit` or repeated
`--template-id` options for focused checks before testing all templates. Use
`--exec-backend sdk` only when the SDK `/execute` service is available in the
template.

## 3. Verify a Patch

```sh
cubesandbox-swe verify \
  --task-json results/swe_infinite_task_1.json \
  --fix-patch results/task1_fix_patch.diff \
  --template-id swe-task1-rubocop-runner-v1
```

The verifier uses the affinetes SWE-INFINITE scaffold and records lifecycle
state for the sandbox. A successful run creates a sandbox, uploads verifier
assets through CubeSandbox exec, pauses the sandbox, reconnects to it, and then
runs the verifier after restore. The result should include:

```text
status=ok
score=1.0
state_after_save=paused
state_after_restore=running
```

## 4. Solve and Verify

```sh
cubesandbox-swe solve \
  --task-json results/swe_infinite_task_1.json \
  --reasoning-effort medium \
  --codex-location sandbox \
  --max-verify-attempts 1
```

The default `sandbox` runtime keeps the Codex CLI process outside the task
sandbox for model access, but the task repository exists only inside
CubeSandbox. Reads, commands, patch application, and diff collection are routed
through CubeSandbox-backed MCP tools inside `/app`. The Codex binary and model
API key are not copied into the task sandbox, and no host-side task repository
mirror is used.

The command checks the Codex CLI and model endpoint before creating a solve
sandbox. Use `--skip-model-preflight` only after separately confirming that the
endpoint works.

Run the same model check without starting a sandbox:

```sh
cubesandbox-swe doctor --model --model-preflight-timeout 60
```

For runtime-only checks of the MCP execution path, run:

```sh
cubesandbox-swe doctor --runtime --codex-runtime-smoke
```

For the full validation ladder, including dependency cleanliness, model
preflight, verifier save/restore, and one real Codex solve, see
[Validation](validation.md).

## 5. Collect SWE50 Trajectories

```sh
cubesandbox-swe collect swe50 \
  --limit 50 \
  --repeats 4 \
  --concurrency 4 \
  --resolver-workers 64 \
  --scan-max 50000
```

The collector is resumable. Existing `result.json` files are skipped unless
`--force` is supplied.
