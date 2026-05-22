# Hint-Invariant SWE Full Experiment

## 1. Objective

Run a complete on-policy-aware SWE experiment to evaluate whether the offline hint-invariant process score predicts real SWE solve outcomes for a Qwen student model.

The experiment must produce a final, auditable report that separates:

1. GPT teacher-state continuation competence.
2. Qwen student-on-policy process competence.
3. High-support teacher-prefix competence.
4. Online SWE verifier outcomes.
5. Baseline comparisons.
6. Statistical uncertainty and conclusion limits.

The primary experimental question is:

> Does offline Goodness = -B on student-on-policy and high-support teacher prefixes predict Qwen online SWE success?

Where:

```text
B = L0 + lambda * max(0, G_plus) + mu * S_irrelevant + nu * max(0, H_misleading)
Goodness = -B
````

`B` is lower-is-better. `Goodness` is higher-is-better.

## 2. Required final artifacts

All outputs must live under:

```text
results/hint_eval_full/<experiment_id>/
```

Required files:

```text
experiment_manifest.json
run_config.resolved.json
git_state.txt
preflight.md

teacher_rollouts/
student_rollouts/

prefixes.jsonl
prefix_support.jsonl
probes.jsonl

provider_check.teacher.json
provider_check.student.json

scores.qwen.jsonl
summary.qwen.json
prefix_group_comparison.json
prefix_group_comparison.md
ablation.qwen.json
ablation.qwen.md

online_results.jsonl
baseline_comparison.json
baseline_comparison.md

final_report.md
artifact_index.md
```

If a real endpoint or runtime is unavailable, produce the same directory structure where possible and write a blocker report instead of silently skipping steps.

## 3. Experiment scale

Use three execution levels.

### Level A: smoke

Purpose: verify the full pipeline.

```text
tasks: 3-5
teacher_rollouts_per_task: 1
student_rollouts_per_task: 1
max_prefixes_per_trajectory: 2
max_candidates: 4
real_scoring_limit: 20 probes
```

### Level B: pilot

Purpose: get an initial real signal.

```text
tasks: 20-50
teacher_rollouts_per_task: 2
student_rollouts_per_task: 2
max_prefixes_per_trajectory: 4
max_candidates: 4
real_scoring_limit: all pilot probes if budget allows
```

### Level C: main

Purpose: produce the main experiment report.

```text
tasks: 100 by default
teacher_rollouts_per_task: 3
student_rollouts_per_task: 3
max_prefixes_per_trajectory: 4
max_candidates: 4
bootstrap_samples: 1000
```

If runtime or endpoint budget is limited, run Level A and Level B completely, then write a clear blocker for Level C.

## 4. Models

### Teacher model

Default:

```text
GPT teacher
```

Purpose:

```text
generate successful or high-quality SWE trajectories
provide future-success evidence for teacher prefixes
provide oracle evidence for student failed prefixes when gold/verifier metadata is insufficient
```

Teacher model configuration must be recorded.

### Student model

Default:

```text
Qwen3.6-27B
```

Purpose:

```text
run online SWE rollouts
score candidate actions under neutral/causal/irrelevant/misleading hints
```

Student endpoint configuration must be recorded without storing API keys.

### Optional variants

If available, include at least 5 variants for model-ranking evidence:

```text
qwen_base
qwen_prompt_v1
qwen_prompt_v2
qwen_sft_checkpoint_1
qwen_sft_checkpoint_2
qwen_sft_checkpoint_3
```

If only one Qwen model is available, the final report may support task-level prediction and failure diagnosis, but not model ranking.

## 5. Environment and task source

Use the existing CubeSandbox/SWE-INFINITE workflow in this repository.

Do not introduce Docker as a required runtime for this experiment.

The experiment runner must inspect current CLI help and use existing commands. Do not invent command flags if they are not supported.

Record:

```text
git commit
git diff
python version
cubesandbox-swe version or package metadata
CubeSandbox runtime status
available templates
task source
task ids
model env var names
```

## 6. Preflight checks

Before collecting expensive trajectories, run:

```sh
SKIP_DOCTOR=1 PYTHON_BIN=.venv/bin/python bash scripts/check.sh

cubesandbox-swe --help
cubesandbox-swe doctor --help
cubesandbox-swe solve --help
cubesandbox-swe verify --help
cubesandbox-swe hint-eval --help
cubesandbox-swe hint-eval collect-prefixes --help
cubesandbox-swe hint-eval support --help
cubesandbox-swe hint-eval build-onpolicy --help
cubesandbox-swe hint-eval score --help
cubesandbox-swe hint-eval analyze --help
cubesandbox-swe hint-eval compare-prefix-groups --help
cubesandbox-swe hint-eval report --help
```

If a command is missing, stop and report the missing capability.

## 7. Runtime preflight

Check whether real online rollouts are possible.

Required checks:

```text
CubeSandbox service reachable
SWE-INFINITE template available or preparable
affinetes verifier path works
teacher model endpoint available
student model endpoint available
Qwen scoring endpoint returns logprobs or supported scoring signals
```

If real Qwen scoring endpoint does not support candidate logprob scoring, either:

1. reduce candidate count,
2. use a supported prompt-logprob scorer,
3. run fake scorer only for pipeline validation,
4. or stop with a scoring-provider blocker.

Do not treat fake scorer output as model evidence.

## 8. Data collection

### 8.1 Task selection

Create a deterministic task manifest:

```text
seed: 0
task_count: from selected scale
task source: existing repo/SWE-INFINITE task source
deduplicate by task_id or instance_id
```

Prefer tasks with:

```text
working verifier
known issue text
available online result extraction
successful or at least usable trajectories
```

Do not hand-pick tasks based on experimental outcome.

### 8.2 Teacher rollouts

For each task:

```text
run teacher_rollouts_per_task GPT attempts
record trajectory
record verifier result
record patch if produced
record resolved/score
```

Use successful teacher trajectories as the primary source for teacher-success prefixes.

If no teacher attempt succeeds, keep failed teacher trajectory only for diagnostics; do not use it as successful future evidence.

### 8.3 Student rollouts

For each task:

```text
run student_rollouts_per_task Qwen attempts
record trajectory
record verifier result
record patch if produced
record resolved/score
```

Use both successful and failed student trajectories for student-on-policy prefixes.

Student failed prefixes are especially important.

## 9. Prefix construction

Run:

```sh
cubesandbox-swe hint-eval collect-prefixes \
  --teacher-trajectory-glob '<experiment_dir>/teacher_rollouts/**/*.json' \
  --student-trajectory-glob '<experiment_dir>/student_rollouts/**/*.json' \
  --online-results-glob '<experiment_dir>/**/*.json' \
  --output '<experiment_dir>/prefixes.jsonl' \
  --max-prefixes-per-trajectory 4 \
  --seed 0
```

Prefix groups:

```text
teacher_success_prefix
student_onpolicy_prefix
student_success_prefix
student_failure_prefix
```

Cutpoint types:

```text
file_localization
diagnosis
edit_decision
verification
stop_decision
```

## 10. Prefix support

Run:

```sh
cubesandbox-swe hint-eval support \
  --prefixes '<experiment_dir>/prefixes.jsonl' \
  --output '<experiment_dir>/prefix_support.jsonl' \
  --student-model 'qwen3.6-27b'
```

Support buckets:

```text
high
medium
low
unknown
```

The final report must separately show:

```text
all_teacher_success_prefix
high_support_teacher_prefix
all_student_onpolicy_prefix
student_success_prefix
student_failure_prefix
```

The primary conclusion must rely on:

```text
student_onpolicy_prefix
high_support_teacher_prefix
```

not on all GPT teacher prefixes.

## 11. Probe construction

Run:

```sh
cubesandbox-swe hint-eval build-onpolicy \
  --prefixes '<experiment_dir>/prefixes.jsonl' \
  --support '<experiment_dir>/prefix_support.jsonl' \
  --output '<experiment_dir>/probes.jsonl' \
  --max-candidates 4 \
  --hint-strength l2 \
  --seed 0
```

Each probe must contain:

```text
neutral hint
causal hint
irrelevant hint
misleading hint
candidate actions
target distribution
prefix source
support bucket
oracle source
quality flags
leakage flags
```

Causal hints must not include exact patch hunks or final diff lines.

Main experiment uses `hint_strength = l2`.

`l3` is allowed only as an ablation or upper bound.

## 12. Scoring

Run provider check first:

```sh
cubesandbox-swe hint-eval provider-check \
  --scorer choice-logprobs \
  --base-url "$QWEN_BASE_URL" \
  --api-key-env QWEN_API_KEY \
  --model "$QWEN_MODEL" \
  --output '<experiment_dir>/provider_check.student.json'
```

Then score probes:

```sh
cubesandbox-swe hint-eval score-batch \
  --probes '<experiment_dir>/probes.jsonl' \
  --output '<experiment_dir>/scores.qwen.jsonl' \
  --scorer choice-logprobs \
  --base-url "$QWEN_BASE_URL" \
  --api-key-env QWEN_API_KEY \
  --model "$QWEN_MODEL" \
  --concurrency 8 \
  --max-retries 3 \
  --retry-backoff 2.0 \
  --cache-dir '<experiment_dir>/cache/qwen' \
  --resume
```

If `score-batch` is unavailable, use existing `score` command and record that batch scoring was unavailable.

Scoring must record:

```text
model
scorer
provider check result
cache stats
errors
condition-level probabilities
```

## 13. Online result extraction

Run:

```sh
cubesandbox-swe hint-eval export online-results \
  --input-glob '<experiment_dir>/**/*.json' \
  --output '<experiment_dir>/online_results.jsonl'
```

If export is unavailable, use the best existing online-result extraction in the repo and document the method.

Online result fields:

```text
task_id
instance_id
trajectory_model
score
resolved
source file
source fields
warnings
```

## 14. Analysis

Run:

```sh
cubesandbox-swe hint-eval analyze \
  --scores '<experiment_dir>/scores.qwen.jsonl' \
  --online-results-glob '<experiment_dir>/**/*.json' \
  --output '<experiment_dir>/summary.qwen.json'
```

Run prefix group comparison:

```sh
cubesandbox-swe hint-eval compare-prefix-groups \
  --scores '<experiment_dir>/scores.qwen.jsonl' \
  --online-results-glob '<experiment_dir>/**/*.json' \
  --output '<experiment_dir>/prefix_group_comparison.json' \
  --markdown '<experiment_dir>/prefix_group_comparison.md'
```

Run ablations if available:

```sh
cubesandbox-swe hint-eval ablate \
  --scores '<experiment_dir>/scores.qwen.jsonl' \
  --output '<experiment_dir>/ablation.qwen.json' \
  --markdown '<experiment_dir>/ablation.qwen.md'
```

## 15. Required metrics

Per probe:

```text
L0
L_plus
G_plus = L0 - L_plus
S_irrelevant
H_misleading
B
Goodness = -B
```

By prefix group:

```text
probe_count
task_count
resolved_count
online_resolve_rate
mean_L0
mean_G_plus
mean_S_irrelevant
mean_H_misleading
mean_B
mean_Goodness
```

Correlation with online result:

```text
Spearman(Goodness, online_score)
Kendall(Goodness, online_score)
Pearson(Goodness, online_score)
pairwise ranking accuracy
bootstrap confidence intervals
```

If only one student model is available, emphasize instance-level prediction rather than model-level ranking.

## 16. Baselines

Compare the composite score against:

```text
no-hint CE only: L0
causal-hint CE only: L_plus
hint-gain only: G_plus
file-localization probability/accuracy
teacher-action imitation proxy if available
prompt-following sensitivity: S_irrelevant and H_misleading
random candidate baseline
```

The final report must say whether `Goodness = -B` is better, similar, or worse than the strongest baseline.

## 17. Statistical rules

Use bootstrap confidence intervals.

Default:

```text
bootstrap_samples: 1000
seed: 0
confidence: 95%
```

Do not tune `lambda`, `mu`, `nu`, hint wording, cutpoint filters, or candidate filters on the final analysis subset.

If tuning is necessary, split into:

```text
dev tasks
final tasks
```

and report this split.

## 18. Leakage and quality checks

Report:

```text
leakage flag count
probe count with zero positives
probe count with no negatives
candidate count distribution
target entropy distribution
hint source distribution
oracle source distribution
support bucket distribution
```

Any probe containing exact patch hunks or final diff lines in the hint must be excluded from the main metric and listed separately.

## 19. Final report

Generate:

```sh
cubesandbox-swe hint-eval report \
  --summary '<experiment_dir>/summary.qwen.json' \
  --scores '<experiment_dir>/scores.qwen.jsonl' \
  --output '<experiment_dir>/final_report.md'
```

If the existing report command does not include all required fields, create or append a manually generated section.

`final_report.md` must include:

1. Executive summary.
2. Experiment configuration.
3. Git/runtime/model preflight.
4. Task selection.
5. Teacher rollout summary.
6. Student rollout summary.
7. Prefix group counts.
8. Support bucket counts.
9. Probe quality summary.
10. Scoring provider summary.
11. Offline metrics by prefix group.
12. Online result summary.
13. Correlation and confidence intervals.
14. Baseline comparison.
15. Ablations.
16. Failure case examples.
17. Valid claims.
18. Invalid claims.
19. Blockers and next steps.

## 20. Claim levels

### Level 0: pipeline only

Condition:

```text
fake scorer only or fixture data only
```

Allowed claim:

```text
The pipeline runs. No model capability claim is supported.
```

### Level 1: real offline scoring only

Condition:

```text
real Qwen scoring, no online verifier join
```

Allowed claim:

```text
The report describes offline process sensitivity, not environment performance.
```

### Level 2: one student model with online results

Condition:

```text
one Qwen model, real online verifier results
```

Allowed claim:

```text
The offline metrics may predict task-level Qwen success/failure and diagnose failure modes.
```

Forbidden claim:

```text
The metric ranks models.
```

### Level 3: multiple student variants

Condition:

```text
at least 5 variants with online results
```

Allowed claim:

```text
The offline metric provides model/scaffold ranking evidence under this environment.
```

### Level 4: strong evidence

Condition:

```text
multiple variants
fresh or contamination-controlled tasks
held-out final split
baselines and ablations included
bootstrap CIs included
no test-set tuning
```

Allowed claim:

```text
The hint-invariant offline score is empirically supported as a proxy under the tested SWE distribution.
```

## 21. Artifact index

Create:

```text
artifact_index.md
```

It should list every important file, its purpose, and whether it is safe to publish.

Do not publish API keys, raw secrets, or oversized caches.

## 22. Completion criteria

The full experiment is complete when:

1. Preflight passes or blockers are documented.
2. Teacher and student rollouts are collected or collection blockers are documented.
3. Prefixes, support diagnostics, probes, scores, online results, and summaries are generated.
4. Metrics are reported separately for teacher, high-support teacher, and student-on-policy prefixes.
5. `Goodness = -B` direction is used correctly.
6. Baselines are included.
7. Bootstrap confidence intervals are included where data size permits.
8. Final report states the strongest valid claim level.
9. Artifact index exists.
10. No secrets or large unintended artifacts are committed.
