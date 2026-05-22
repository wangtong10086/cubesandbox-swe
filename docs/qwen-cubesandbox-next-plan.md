# Qwen + CubeSandbox SWE Next Plan

## 1. Objective

Resolve the current blockers from `docs/qwen-cubesandbox-experiment-issues.md` and produce a clean, reproducible pilot experiment.

The goal is not yet to prove Qwen SWE competence or model ranking. The goal is to reach:

```text
clean repo state
safe local-proxy runtime
stable task id extraction
one debugged task 11827 retry
fresh pilot experiment directory
5-10 deterministic Qwen student rollouts
regenerated hint-eval V2 artifacts
clean final pilot report
```

The pilot should clearly separate:

```text
teacher_success_prefix
high_support_teacher_prefix
student_onpolicy_prefix
```

and must report `Goodness = -B` as higher-is-better.

## 2. Current status

Known from the current issue report:

1. Qwen/Codex Responses API compatibility is no longer the main blocker.
2. Local Qwen proxy works for model preflight and offline scoring with `--api-key-env no-auth`.
3. One real Qwen + CubeSandbox rollout finished for task `11827`, but verifier score was `0.0`.
4. Pilot/main scale are still blocked because only one real Qwen rollout exists.
5. Some trajectory rows have weak or missing `task_id`.
6. Future runtime artifacts may still capture sensitive environment values.
7. The current result directory mixes fixture smoke data and one real Qwen rollout.
8. The working tree needs review before continuing.

## 3. Non-goals

Do not attempt the full 100-task main experiment in this phase.

Do not claim:

```text
Qwen solves SWE tasks reliably
offline metric validates environment competence
model ranking evidence
```

Do not commit:

```text
large results/
large logs/
runtime caches/
secrets/
raw Codex sqlite/cache artifacts
```

Do not modify:

```text
third_party/CubeSandbox
third_party/affinetes
```

## 4. Milestones

## M0: Repository hygiene and safety

### Tasks

1. Inspect `git status`.
2. Decide whether `TODO.md` deletion is intentional.
3. Review and commit only source/doc/test changes needed for the working proxy/runtime/evaluator path.
4. Keep generated `results/`, `logs/`, and large runtime artifacts untracked.
5. Confirm no changes under:

```text
third_party/CubeSandbox
third_party/affinetes
```

1. Run validation:

```sh
SKIP_DOCTOR=1 PYTHON_BIN=.venv/bin/python bash scripts/check.sh
```

1. Run exact-value `.env` secret scan over any result directory that may be shared.

### Acceptance

```text
scripts/check.sh passes
third_party diff is empty
no secrets detected in shared artifacts
source changes are reviewable as one logical commit
```

## M1: Fix task id derivation

### Problem

Some summary rows show `task_id: null` even when the task filename or metadata contains the id. The known regression target is:

```text
task_00000011827.json -> 11827
```

### Tasks

1. Add task id derivation from, in order:

```text
explicit task_id field
instance_id field
task_json filename
result metadata
rollout bucket metadata
trajectory path
```

1. Add regression tests for task `11827`.
2. Ensure prefix/probe/summary rows carry the derived task id.

### Acceptance

```text
task_00000011827.json derives task_id 11827
prefixes.jsonl has task_id for task 11827
probes.jsonl has task_id for task 11827
summary rows do not show null task_id when recoverable
tests pass
```

## M2: Keep and harden the local Qwen proxy path

### Decision

Use the local proxy path as the default working path.

Do not block the experiment on direct upstream no-auth, because the issue report says direct upstream no-auth still returns HTTP 401 while local proxy no-auth works.

### Tasks

1. Review:

```text
scripts/qwen_responses_namespace_proxy.py
cubesandbox_swe/hint_eval/scoring.py
cubesandbox_swe/hint_eval/cli.py
cubesandbox_swe/legacy/run_cubesandbox_codex_swe_e2e.py
```

1. Ensure localhost/127.0.0.1 model endpoints use:

```text
CODEX_API_KEY=no-auth
```

instead of passing real API keys into Codex.

1. Add or update tests for no-auth scoring and no-auth local solve path.
2. Run provider check against the local proxy if available:

```sh
cubesandbox-swe hint-eval provider-check \
  --scorer choice-logprobs \
  --base-url http://127.0.0.1:18088/v1 \
  --api-key-env no-auth \
  --model "$QWEN_MODEL" \
  --output results/qwen_next_plan/provider_check.local_proxy_noauth.json
```

### Acceptance

```text
local proxy provider check passes when endpoint is available
no real API keys are passed into Codex for localhost endpoint
no-auth scoring tests pass
direct upstream no-auth failure is documented but not blocking
```

## M3: Debug task 11827 before scaling

### Goal

Understand why Qwen failed task `11827`, then retry once with a tighter but still legitimate task prompt.

### Tasks

1. Inspect failed Qwen patch:

```sh
sed -n '1,220p' \
  results/hint_eval_full/full_20260522T162240Z_5da7a19/student_rollouts/qwen_proxy_task11827/cubesandbox_codex_fix_patch_qwen-proxy-task11827-retry2_attempt1.diff
```

1. Inspect successful GPT teacher evidence:

```sh
rg -n "diff --git|API_ACCESS_TOKEN|api_access_token|Authorization|Bearer" \
  results/hint_eval_full/full_20260522T162240Z_5da7a19/teacher_rollouts/gpt55_task11827_rep_0.json
```

1. Compare against expected behavior:

```text
run_sse_client accepts api_access_token: str | None = None
__main__ reads API_ACCESS_TOKEN
__main__ forwards it to run_sse_client
when token exists, downstream SSE client receives headers={"Authorization": "Bearer <token>"}
when token is absent, Authorization header is absent
```

1. Create a targeted retry prompt that mentions:

```text
src/mcp_proxy/__init__.py
src/mcp_proxy/__main__.py
API_ACCESS_TOKEN
headers={"Authorization": "Bearer ..."}
```

1. Retry task `11827` once in a fresh result directory.

### Acceptance

```text
new task 11827 retry trajectory exists
verifier result exists
patch exists if produced
result is clearly marked pass/fail
failure analysis is written if verifier score remains 0.0
```

## M4: Create a fresh clean pilot directory

### Rule

Do not reuse:

```text
results/hint_eval_full/full_20260522T162240Z_5da7a19
```

That directory is only debugging evidence.

### New directory

Use:

```text
results/hint_eval_full/pilot_<timestamp>/
```

### Required files

```text
run_config.resolved.json
git_state.txt
preflight.md
teacher_rollouts/
student_rollouts/
prefixes.jsonl
prefix_support.jsonl
probes.jsonl
scores.qwen.jsonl
summary.qwen.json
prefix_group_comparison.json
prefix_group_comparison.md
online_results.jsonl
final_report.md
artifact_index.md
BLOCKER.md if needed
```

## M5: Run a deterministic 5-10 task pilot

### Task selection

Select a deterministic small task set:

```text
seed: 0
task_count: 5-10
include task 11827 only if useful as debug anchor
do not hand-pick based on Qwen outcome
```

### Rollouts

For each selected task:

```text
teacher_rollouts_per_task: 1 if existing successful teacher trajectory is available, otherwise skip teacher future evidence
student_rollouts_per_task: 1
```

Use Qwen local proxy path.

### Acceptance

```text
at least 5 real Qwen trajectories attempted
each trajectory has verifier result or explicit runtime blocker
successful and failed trajectories are both preserved if available
```

## M6: Regenerate hint-eval V2 artifacts

Run from the fresh pilot directory.

### Prefix collection

```sh
cubesandbox-swe hint-eval collect-prefixes \
  --teacher-trajectory-glob '<pilot_dir>/teacher_rollouts/**/*.json' \
  --student-trajectory-glob '<pilot_dir>/student_rollouts/**/*.json' \
  --online-results-glob '<pilot_dir>/**/*.json' \
  --output '<pilot_dir>/prefixes.jsonl' \
  --max-prefixes-per-trajectory 4 \
  --seed 0
```

### Support

```sh
cubesandbox-swe hint-eval support \
  --prefixes '<pilot_dir>/prefixes.jsonl' \
  --output '<pilot_dir>/prefix_support.jsonl' \
  --student-model 'qwen3.6-27b'
```

### Build probes

```sh
cubesandbox-swe hint-eval build-onpolicy \
  --prefixes '<pilot_dir>/prefixes.jsonl' \
  --support '<pilot_dir>/prefix_support.jsonl' \
  --output '<pilot_dir>/probes.jsonl' \
  --max-candidates 4 \
  --hint-strength l2 \
  --seed 0
```

### Score

Use the local proxy:

```sh
cubesandbox-swe hint-eval score \
  --probes '<pilot_dir>/probes.jsonl' \
  --output '<pilot_dir>/scores.qwen.jsonl' \
  --scorer choice-logprobs \
  --base-url http://127.0.0.1:18088/v1 \
  --api-key-env no-auth \
  --model "$QWEN_MODEL"
```

If `score-batch` exists and is stable, use it instead.

### Online results

```sh
cubesandbox-swe hint-eval export online-results \
  --input-glob '<pilot_dir>/**/*.json' \
  --output '<pilot_dir>/online_results.jsonl'
```

### Analyze

```sh
cubesandbox-swe hint-eval analyze \
  --scores '<pilot_dir>/scores.qwen.jsonl' \
  --online-results-glob '<pilot_dir>/**/*.json' \
  --output '<pilot_dir>/summary.qwen.json'
```

### Prefix group comparison

```sh
cubesandbox-swe hint-eval compare-prefix-groups \
  --scores '<pilot_dir>/scores.qwen.jsonl' \
  --online-results-glob '<pilot_dir>/**/*.json' \
  --output '<pilot_dir>/prefix_group_comparison.json' \
  --markdown '<pilot_dir>/prefix_group_comparison.md'
```

### Report

```sh
cubesandbox-swe hint-eval report \
  --summary '<pilot_dir>/summary.qwen.json' \
  --scores '<pilot_dir>/scores.qwen.jsonl' \
  --output '<pilot_dir>/final_report.md'
```

## M7: Pilot report requirements

`final_report.md` must include:

1. Executive summary.
2. What was fixed since the blocker report.
3. Runtime and proxy configuration.
4. Task set.
5. Qwen rollout summary.
6. Task 11827 debug result.
7. Prefix counts by group.
8. Support bucket counts.
9. Probe quality and leakage flags.
10. Qwen scoring summary.
11. Online verifier summary.
12. Metrics for:

```text
teacher_success_prefix
high_support_teacher_prefix
student_onpolicy_prefix
student_success_prefix
student_failure_prefix
```

1. Baseline comparison if available.
2. Claim level.
3. Remaining blockers.
4. Next recommended scale.

## 8. Claim level

Unless at least five model/scaffold/checkpoint variants are evaluated, do not claim model ranking.

For this pilot, expected maximum claim level is:

```text
Level 2: one Qwen student model with online results
```

Allowed claim:

```text
The offline metrics may help predict task-level Qwen success/failure and diagnose failure modes.
```

Forbidden claim:

```text
The metric ranks models.
The metric proves Qwen SWE competence.
The metric validates environment competence generally.
```

## 9. Final validation

Run:

```sh
SKIP_DOCTOR=1 PYTHON_BIN=.venv/bin/python bash scripts/check.sh
git diff --name-only -- third_party/CubeSandbox third_party/affinetes
```

Run exact `.env` value scan over:

```text
<pilot_dir>
```

Prune:

```text
large Codex caches
sqlite shell snapshots
plugin caches
temporary runtime state
```

before sharing artifacts.

## 10. Completion criteria

This plan is complete when:

1. Working tree source changes are reviewed and safe.
2. `scripts/check.sh` passes.
3. task id derivation bug is fixed and tested.
4. local proxy no-auth path is preserved and tested.
5. task `11827` has a debug retry result or a clear blocker.
6. a fresh pilot directory exists.
7. at least 5 Qwen student rollouts are attempted, or a runtime blocker is documented.
8. V2 artifacts are regenerated in the fresh pilot directory.
9. `final_report.md` clearly states claim level and blockers.
10. no secrets or large unintended artifacts are staged.
