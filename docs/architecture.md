# Architecture

`cubesandbox-swe` has three boundaries:

1. The integration layer in `cubesandbox_swe/`.
2. Upstream dependencies in `third_party/`.
3. Local artifacts in ignored runtime directories.

## Data Flow

```text
SWE task JSON
  -> CubeSandbox task template
  -> Codex CLI with CubeSandbox MCP runtime
  -> fix patch
  -> affinetes verifier scaffold
  -> result.json, trajectory.json, rollout_bucket.json
```

The solver and verifier both target the same `/app` task workspace contract
used by the SWE-INFINITE Docker flow. CubeSandbox replaces Docker as the
execution backend while preserving the affinetes patch and test scaffold.
Verifier assets and logs are moved through CubeSandbox exec commands under
`/workspace/cubesandbox-swe`, so verification does not require host mounts or a
host-side copy of the task repository.

## Runtime Isolation

The Codex CLI process stays outside the task sandbox only because it needs
network access to the model endpoint. The task repository is not mounted or
copied to the host. During `solve --codex-location sandbox`, Codex receives an
MCP server whose tools execute repository operations inside CubeSandbox:

- `cube_run`: run commands in `/app`.
- `cube_read_file`: read files from `/app`.
- `cube_apply_patch`: apply patches to `/app`.
- `cube_diff`: collect the source diff from `/app`.

The prompt instructs Codex to use those tools for task work. The configured
Codex sandbox mode is `read-only` for the host control directory, so ordinary
task reads and writes are represented by CubeSandbox MCP calls rather than host
filesystem access.

## Sandbox Lifecycle

The verifier exercises the lifecycle used for save/restore checks:

```text
Sandbox.create(template=...)
  -> upload verifier assets through cubecli exec
  -> write /workspace/cubesandbox-swe/ready_for_save
  -> sandbox.pause()
  -> Sandbox.connect(sandbox_id)
  -> run verifier script after reconnect
  -> sandbox.kill()
```

In this project, `save` means pausing the prepared CubeSandbox instance and
recording a lightweight handle. `restore` means reconnecting to that paused
instance with the upstream SDK. No local SDK patch is required for that path.
Verifier result files must record `state_after_save=paused` and
`state_after_restore=running` before a run is considered successful.

## Subsystems

- `templates`: creates CubeSandbox templates from task images and smoke-tests them.
- `verify`: injects `test_patch`, `augmented_test_patch`, `fix_patch`, canaries, and `test_command`.
- `solve`: runs Codex on the host for model access while all task operations go through CubeSandbox MCP tools under `/app`.
- `collect`: resolves task metadata and repeats solve/verify runs across selected tasks.
- `artifacts`: records what local outputs exist and where full published data lives.

## Upstream Dependencies

CubeSandbox and affinetes are consumed as unmodified upstream checkouts. Local
compatibility code lives in `cubesandbox_swe/`: sandbox save/restore uses the
upstream `pause()` and `connect()` APIs, Codex prompt parsing is owned locally,
and affinetes verifier behavior is adapted by wrapper code rather than by
editing `third_party/`.

The expected clean state is:

```sh
git -C third_party/CubeSandbox status --short
git -C third_party/affinetes status --short
```

Both commands should print nothing.
