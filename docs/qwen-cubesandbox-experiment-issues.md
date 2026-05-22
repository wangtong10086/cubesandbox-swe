# Qwen + CubeSandbox SWE Experiment Issues

Date: 2026-05-23

This document summarizes the current problems, completed fixes, remaining blockers, and suggested next steps for the Hint-Invariant SWE full experiment using Qwen through CubeSandbox.

Primary result directory:

`results/hint_eval_full/full_20260522T162240Z_5da7a19`

Primary reports:

- `results/hint_eval_full/full_20260522T162240Z_5da7a19/final_report.md`
- `results/hint_eval_full/full_20260522T162240Z_5da7a19/BLOCKER.md`
- `results/hint_eval_full/full_20260522T162240Z_5da7a19/artifact_index.md`

## Current Status

The experiment is no longer blocked by Qwen/Codex Responses API compatibility. The local Qwen proxy path works for both model preflight and offline probe scoring with `--api-key-env no-auth`.

One real Qwen + CubeSandbox SWE rollout was completed for task `11827`. It produced a patch and trajectory, but failed online verification with score `0.0`. Therefore, the current claim level is limited to single-task rollout evidence plus pipeline validation, not Qwen SWE competence and not model ranking.

## Completed

1. Removed the host-mirror dependency from the active SWE solve path.

   Codex is used as the orchestration process, while repository operations are routed through CubeSandbox MCP tools:

   - `cube_run`
   - `cube_read_file`
   - `cube_apply_patch`
   - `cube_diff`

   The task repository is accessed inside CubeSandbox at `/app`; the active solve path does not rely on mounting or mirroring the task repo into the host workspace.

2. Qwen Responses API compatibility was worked around with the local proxy.

   Evidence:

   - `preflight/doctor-model-qwen-proxy.summary.json`
   - `provider_check.student.local_proxy_noauth.json`

   Current working scoring path:

   ```bash
   cubesandbox-swe hint-eval score \
     --scorer choice-logprobs \
     --base-url http://127.0.0.1:18088/v1 \
     --api-key-env no-auth
   ```

3. No-auth scoring support was added.

   Code paths:

   - `cubesandbox_swe/hint_eval/scoring.py`
   - `cubesandbox_swe/hint_eval/cli.py`
   - `tests/test_hint_eval_scoring.py`

   `choice-logprobs` can now call endpoints that do not expect an `Authorization` header.

4. Local proxy solve no longer passes real API keys into Codex.

   For localhost/127.0.0.1 model endpoints, `run_cubesandbox_codex_swe_e2e.py` now uses `CODEX_API_KEY=no-auth`.

   This avoids leaking `.env` model keys into Codex shell snapshots when the local proxy does not require a key.

5. A real Qwen rollout was completed.

   The first real attempt timed out after 600 seconds before producing a trajectory.

   The second attempt completed:

   - Run id: `qwen-proxy-task11827-retry2`
   - Task: `11827`
   - Model: `Qwen/Qwen3.6-27B`
   - Result: failed verifier, score `0.0`
   - Trajectory: `student_rollouts/qwen_proxy_task11827/cubesandbox_codex_trajectory_qwen-proxy-task11827-retry2.json`

6. Hint-Invariant V2 artifacts were regenerated with the real Qwen failed trajectory included.

   Current counts:

   - Prefixes: `16`
   - Support diagnostics: `16`
   - Probes: `16`
   - Qwen score records: `16`
   - Online result files: `3`

   Prefix groups are reported separately:

   - `teacher_success_prefix`
   - `high_support_teacher_prefix`
   - `student_onpolicy_prefix`

7. Legacy Codex trajectory parsing was extended.

   Historical GPT teacher trajectories used `command_execution` and `file_change` event shapes rather than only MCP `mcp_tool_call` events. The trajectory adapter now maps those events into abstract SWE actions so teacher prefixes can be collected from the existing trajectories.

   Code paths:

   - `cubesandbox_swe/hint_eval/trajectory.py`
   - `tests/test_hint_eval_trajectory.py`

8. CubeSandbox pause/restore behavior was repaired for tests and runtime tolerance.

   `restore_sandbox_state` now prefers the upstream public `Sandbox.connect` API, and only uses an extended-timeout fallback if the SDK call times out.

   Code path:

   - `cubesandbox_swe/cubesandbox_lifecycle.py`

9. Validation currently passes.

   Latest validation:

   ```bash
   SKIP_DOCTOR=1 PYTHON_BIN=.venv/bin/python bash scripts/check.sh
   ```

   Result:

   - `81 passed`
   - sdist build succeeded
   - wheel build succeeded

10. Generated artifacts were pruned and secret-scanned.

   The raw Codex runtime cache was reduced from roughly 1GB to about 2.4MB by removing large temporary cache/plugin directories after extracting the useful trajectory/result artifacts.

   Latest exact `.env` secret scan:

   - scanned secret-like values: `2`
   - hits: `0`

   Redaction evidence:

   - `results/hint_eval_full/full_20260522T162240Z_5da7a19/logs/redaction_report.json`

## Remaining Problems

1. The real Qwen rollout did not solve task `11827`.

   Evidence:

   - `student_rollouts/qwen_proxy_task11827/cubesandbox_codex_swe_e2e_qwen-proxy-task11827-retry2.json`

   Verifier result:

   - status: `failed`
   - score: `0.0`
   - missing tests:
     - `tests/test_augmented_auth_token.py::test_main_passes_api_access_token_from_env`
     - `tests/test_augmented_auth_token.py::test_main_passes_none_when_no_api_access_token`

   Suggested investigation:

   - Inspect the generated patch:
     `student_rollouts/qwen_proxy_task11827/cubesandbox_codex_fix_patch_qwen-proxy-task11827-retry2_attempt1.diff`
   - Compare it against a known successful GPT teacher patch under:
     `teacher_rollouts/gpt55_task11827_rep_0.json`
   - Check whether Qwen edited the wrong call signature, omitted `API_ACCESS_TOKEN`, or passed headers in the wrong shape.

2. There is no successful real Qwen online rollout yet.

   This blocks any claim that Qwen can solve the selected SWE task set in this runtime.

   Suggested next step:

   - Retry task `11827` with a stronger prompt that explicitly names:
     - `src/mcp_proxy/__init__.py`
     - `src/mcp_proxy/__main__.py`
     - `API_ACCESS_TOKEN`
     - `headers={"Authorization": "Bearer <token>"}`
   - Alternatively, run a small deterministic task set and collect multiple Qwen trajectories before tuning on one task.

3. Pilot and main scale remain blocked.

   Current scale is smoke plus one real Qwen rollout. Pilot/main require substantially more real trajectories.

   Minimum next milestone:

   - select a deterministic small task set
   - run at least one Qwen student rollout per task
   - include successful and failed student trajectories
   - regenerate prefixes/support/probes/scores/summary/report

4. Model ranking is explicitly unsupported.

   Fewer than five model/scaffold/checkpoint variants were evaluated. The report must not be read as ranking evidence.

   To support ranking claims, evaluate at least five real variants under the same task set and scoring/report pipeline.

5. Direct upstream no-auth still returned HTTP 401.

   The working path is the local proxy:

   - direct upstream no-auth: failed with HTTP 401
   - local proxy no-auth: works

   Suggested next step:

   - Keep using the local proxy for Codex and scoring.
   - If direct upstream no-auth is expected to work, inspect the upstream URL and proxy headers separately, but do not block the experiment on direct upstream access.

6. The current result mixes fixture smoke data and one real Qwen rollout.

   This is acceptable for pipeline validation, but it is not a clean full-scale experiment.

   Suggested next step:

   - Create a fresh experiment id when running pilot scale.
   - Keep fixture smoke and real experiment outputs in separate directories.
   - Preserve the current directory only as audit evidence for this debugging phase.

7. Some task identifiers are weak in the current trajectory schema.

   The real task `11827` has `instance_id`, but some summary rows show `task_id: null` because the trajectory does not carry an explicit `task_id` field.

   Suggested fix:

   - Add task id derivation from `task_json` filename, result metadata, or rollout bucket metadata.
   - Add a regression test so `task_00000011827.json` becomes task id `11827` in prefix/probe/summary rows.

8. Runtime artifacts can still capture sensitive environment values in future runs.

   The current run is scanned and redacted, but each new Codex run can create shell snapshots and sqlite state files.

   Required practice for future runs:

   - prefer local proxy `no-auth` mode
   - do not pass real API keys into Codex when the endpoint is local
   - run exact-value `.env` secret scan after every real rollout
   - prune large Codex runtime caches before sharing artifacts

9. The working tree is not clean.

   Current notable uncommitted state includes:

   - deleted `TODO.md`
   - modified runtime/doctor/hint-eval files
   - untracked `docs/hint-invariant-full-experiment.md`
   - untracked local proxy/rental scripts
   - untracked `tests/test_hint_eval_trajectory.py`

   Suggested next step:

   - Decide whether `TODO.md` deletion is intentional.
   - Review `scripts/qwen_responses_namespace_proxy.py` before committing, because it is currently important to the successful proxy path.
   - Keep generated `results/` artifacts uncommitted unless a small curated sample is intentionally added.

## Suggested Debug Plan For Qwen Task 11827

1. Inspect the failed Qwen patch.

   ```bash
   sed -n '1,220p' \
     results/hint_eval_full/full_20260522T162240Z_5da7a19/student_rollouts/qwen_proxy_task11827/cubesandbox_codex_fix_patch_qwen-proxy-task11827-retry2_attempt1.diff
   ```

2. Inspect a successful teacher trajectory and patch.

   ```bash
   rg -n "diff --git|API_ACCESS_TOKEN|api_access_token|Authorization|Bearer" \
     results/hint_eval_full/full_20260522T162240Z_5da7a19/teacher_rollouts/gpt55_task11827_rep_0.json
   ```

3. Compare the expected implementation shape.

   Expected behavior:

   - `run_sse_client` accepts `api_access_token: str | None = None`
   - when token is a string, downstream SSE client receives:
     `headers={"Authorization": "Bearer <token>"}`
   - when token is absent, `Authorization` is not present in headers
   - `__main__` reads `API_ACCESS_TOKEN` and forwards it to `run_sse_client`

4. Retry with a tighter task prompt.

   The current generic prompt allowed Qwen to produce a patch, but not a passing one. A targeted retry prompt can mention the exact files and the exact I/O contract while still restricting edits to source files.

5. After retry, regenerate only the affected experiment artifacts.

   ```bash
   cubesandbox-swe hint-eval collect-prefixes ...
   cubesandbox-swe hint-eval support ...
   cubesandbox-swe hint-eval build-onpolicy ...
   cubesandbox-swe hint-eval score --api-key-env no-auth ...
   cubesandbox-swe hint-eval analyze ...
   cubesandbox-swe hint-eval compare-prefix-groups ...
   cubesandbox-swe hint-eval report ...
   ```

6. Re-run validation and safety checks.

   ```bash
   SKIP_DOCTOR=1 PYTHON_BIN=.venv/bin/python bash scripts/check.sh
   git diff --name-only -- third_party/CubeSandbox third_party/affinetes
   ```

   Also run an exact `.env` value scan over the new result directory before publishing or sharing artifacts.

## Files To Review Before Committing

Review these code changes as one logical group:

- `cubesandbox_swe/hint_eval/scoring.py`
- `cubesandbox_swe/hint_eval/cli.py`
- `cubesandbox_swe/hint_eval/trajectory.py`
- `cubesandbox_swe/legacy/run_cubesandbox_codex_swe_e2e.py`
- `cubesandbox_swe/cubesandbox_lifecycle.py`
- `tests/test_hint_eval_scoring.py`
- `tests/test_codex_e2e_config.py`
- `tests/test_hint_eval_trajectory.py`

Review these local/user files separately before deciding whether to commit:

- `docs/hint-invariant-full-experiment.md`
- `scripts/qwen_responses_namespace_proxy.py`
- `scripts/targon_qwen36_rental.py`
- `TODO.md` deletion
