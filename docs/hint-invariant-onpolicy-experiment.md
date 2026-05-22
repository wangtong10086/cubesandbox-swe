# Hint-Invariant SWE Experiment V2: On-Policy-Aware Evaluation

## 1. Goal

This experiment evaluates whether the hint-invariant offline process score can predict SWE agent performance while explicitly controlling for prefix distribution mismatch between the teacher model and the student model.

The previous MVP builds probes from successful teacher trajectories. That is useful, but it only measures student behavior on teacher-visited states. In this V2 experiment, we add student-on-policy prefixes and prefix-support diagnostics.

The core question is:

> Does the student model assign high probability to good SWE actions without privileged hints in the states it actually visits, and does this offline process score predict online SWE outcomes?

## 2. Why this change is necessary

If teacher = GPT and student = Qwen, then GPT trajectory prefixes may be low-probability or unreachable under Qwen.

The original teacher-prefix score estimates:

```text
student behavior on GPT teacher states
````

but the real online SWE process depends on:

```text
student behavior on Qwen-induced states
```

This is a standard covariate-shift issue in sequential decision-making. Future observations depend on earlier actions, so evaluating only on expert prefixes can overestimate or mischaracterize learner behavior.

Therefore, V2 reports three separate distributions:

1. `teacher_success_prefix`
2. `student_onpolicy_prefix`
3. `high_support_teacher_prefix`

The primary claim should use `student_onpolicy_prefix` and `high_support_teacher_prefix`, not all teacher prefixes.

## 3. Experimental units

Each experimental unit is a SWE task instance.

For each task, collect:

```text
GPT teacher trajectories
Qwen student trajectories
online verifier result for Qwen
optional online verifier result for GPT
patch/test/gold metadata if available
```

The SWE online result is higher-is-better:

```text
resolved = true/false
score = 0/1 or continuous verifier score
```

The offline burden score is lower-is-better:

```text
B = L0 + lambda * max(0, G_plus) + mu * S_irrelevant + nu * max(0, H_misleading)
```

For correlations, report either:

```text
offline_goodness = -B
```

or explicitly report that `B` should be negatively correlated with online score.

## 4. Prefix sources

### 4.1 Teacher-success prefixes

Source:

```text
successful GPT teacher trajectories
```

Use:

```text
measure whether Qwen can continue from high-quality GPT states
```

Valid claim:

```text
Qwen continuation competence on GPT-success states
```

Invalid claim:

```text
Qwen will solve the task online
```

### 4.2 Student-on-policy prefixes

Source:

```text
Qwen rollout trajectories, including both successful and failed runs
```

Use:

```text
measure Qwen's process competence in states it actually visits
```

This is the primary evaluation distribution.

For each Qwen trajectory, choose cutpoints such as:

```text
before first source-file inspection
after failing test/error observation
before first edit
after first edit before verification
before stop/submission
```

### 4.3 High-support teacher prefixes

Source:

```text
subset of GPT teacher prefixes that look similar to states Qwen also reaches
```

Use:

```text
reduce teacher/student distribution mismatch
```

A teacher prefix is high-support if Qwen trajectories on the same task or similar tasks show similar abstract state features.

## 5. Prefix support diagnostics

For each probe, compute:

```json
{
  "prefix_source": "teacher_success|student_onpolicy",
  "student_model": "qwen3.6-27b",
  "support_bucket": "high|medium|low|unknown",
  "state_feature_overlap": 0.0,
  "abstract_action_overlap": 0.0,
  "opened_gold_file": true,
  "ran_failing_test": true,
  "has_patch": false,
  "has_seen_error": true,
  "student_reached_similar_state": true
}
```

Recommended abstract state features:

```text
opened_files
opened_patch_touched_file
opened_test_file
searched_symbol
ran_test
saw_error
has_patch
ran_verification
stopped
step_index_bucket
```

Do not rely only on exact action logprob, because GPT and Qwen may use different shell commands for the same abstract operation.

## 6. Hint generation

Each probe still has four conditions:

```text
neutral
causal
irrelevant
misleading
```

### 6.1 For teacher-success prefixes

Causal hint comes from:

```text
future successful GPT actions
patch-touched files
future verification commands
future relevant inspect/search/edit actions
```

Example:

```text
The later successful path focuses on the configuration-loading code before editing.
```

### 6.2 For student-on-policy prefixes

Causal hint cannot depend on Qwen's future if Qwen failed. Instead derive it from oracle evidence:

```text
gold patch touched files, if available
fail-to-pass tests, if available
GPT successful trajectory for the same task
verifier metadata
optional GPT correction from the Qwen prefix
```

Recommended order:

1. Use gold/verifier metadata if present.
2. Otherwise use GPT successful trajectory for the same task.
3. Optionally use GPT as a labeler/corrector, but mark the probe source clearly.

The hint must not include exact patch hunks or final diff lines.

### 6.3 Irrelevant hint

A plausible but task-irrelevant SWE note.

Example:

```text
Project documentation may be useful context for understanding installation behavior.
```

### 6.4 Misleading hint

A plausible wrong direction derived from a negative candidate.

Example:

```text
A plausible next step is to inspect packaging metadata rather than the failing module.
```

Misleading hints should be realistic, not obviously silly.

## 7. Candidate actions

Keep the candidate set small and stable.

Default maximum:

```text
4 candidates
```

Primary candidate levels:

1. file-level
2. operation-level

Command-level candidates are optional and should not be the primary metric.

Positive candidates can come from:

```text
future successful teacher actions
gold patch touched files
relevant failing tests
student successful future actions
verifier-approved actions
```

Negative candidates can come from:

```text
student failed actions
plausible unrelated files
same-repo distractor files
generic but unhelpful SWE operations
```

Each probe should record:

```json
{
  "positive_count": 2,
  "negative_count": 2,
  "candidate_count": 4,
  "candidate_kind": "file|operation|mixed",
  "target_distribution_entropy": 0.69
}
```

## 8. Metrics

For each probe:

```text
L0            = neutral/no-hint CE
L_plus        = causal-hint CE
G_plus        = L0 - L_plus
S_irrelevant  = sensitivity to irrelevant hint
H_misleading  = harm from misleading hint
B             = composite burden, lower is better
Goodness      = -B, higher is better
```

Default:

```text
lambda = 0.5
mu = 0.25
nu = 0.25
```

Do not tune these weights on the final test split.

## 9. Required analysis views

Report metrics separately for:

```text
all_teacher_success_prefix
high_support_teacher_prefix
all_student_onpolicy_prefix
student_success_prefix
student_failure_prefix
```

Primary table:

```text
prefix_group
probe_count
task_count
L0
G_plus
S_irrelevant
H_misleading
B
Goodness = -B
online_resolve_rate
Spearman(Goodness, online_score)
Kendall(Goodness, online_score)
pairwise_accuracy
```

The main result should be based on:

```text
student_onpolicy_prefix
high_support_teacher_prefix
```

not on all teacher prefixes.

## 10. Baselines

Compare the proposed score against:

```text
teacher-action imitation NLL
no-hint CE only
causal-hint CE only
hint-gain only
file-localization accuracy/probability
prompt-following sensitivity
random candidate baseline
```

If `B` does not outperform `L0` or file-localization baselines, the conclusion should say so.

## 11. Valid claims

### With one student model

If only Qwen3.6-27B is evaluated, the experiment can support:

```text
task-level prediction
failure diagnosis
prefix-distribution analysis
hint sensitivity analysis
```

It cannot support:

```text
model ranking
```

### With multiple Qwen variants

If there are at least 5 model/scaffold/checkpoint variants, the experiment can support:

```text
model-level ranking evidence
```

### With fresh or private tasks

If tasks are fresh, private, or contamination-controlled, the conclusion is stronger.

Public SWE-bench Verified can be used for comparability, but it should not be the only evidence because public SWE benchmarks can suffer from contamination and test-validity issues.

## 12. Minimal experiment size

For a useful smoke experiment:

```text
20-50 tasks
1 Qwen model
1 GPT teacher
1-3 Qwen rollouts per task
1-3 GPT teacher rollouts per task
2-4 probes per trajectory
```

For a stronger experiment:

```text
100-300 tasks
5+ Qwen/scaffold/checkpoint variants
3+ Qwen rollouts per task
3+ GPT teacher rollouts per task
fresh/private split if available
bootstrap confidence intervals
```

## 13. CLI additions

Add a compact V2 CLI surface:

```sh
cubesandbox-swe hint-eval collect-prefixes --help
cubesandbox-swe hint-eval build-onpolicy --help
cubesandbox-swe hint-eval support --help
cubesandbox-swe hint-eval compare-prefix-groups --help
```

Existing commands must remain working:

```sh
cubesandbox-swe hint-eval build
cubesandbox-swe hint-eval score
cubesandbox-swe hint-eval analyze
cubesandbox-swe hint-eval report
```

## 14. New commands

### 14.1 collect-prefixes

Collect and normalize prefixes from teacher and student trajectories.

Example:

```sh
cubesandbox-swe hint-eval collect-prefixes \
  --teacher-trajectory-glob 'results/teacher_gpt/**/*.json' \
  --student-trajectory-glob 'results/student_qwen/**/*.json' \
  --online-results-glob 'results/**/*.json' \
  --output results/hint_eval_v2/prefixes.jsonl \
  --max-prefixes-per-trajectory 4 \
  --seed 0
```

Each prefix record should include:

```json
{
  "schema_version": "hint_eval_prefix_v2",
  "prefix_id": "...",
  "task_id": "...",
  "instance_id": "...",
  "repo": "...",
  "prefix_source": "teacher_success|student_onpolicy",
  "trajectory_file": "...",
  "trajectory_model": "gpt|qwen",
  "trajectory_resolved": true,
  "cutpoint_type": "file_localization|diagnosis|edit_decision|verification|stop_decision",
  "prefix_messages": [],
  "observed_state_features": {},
  "future_actions": [],
  "patch_metadata": {},
  "online_result": {}
}
```

### 14.2 support

Compute support diagnostics for teacher prefixes using student prefixes.

Example:

```sh
cubesandbox-swe hint-eval support \
  --prefixes results/hint_eval_v2/prefixes.jsonl \
  --output results/hint_eval_v2/prefix_support.jsonl \
  --student-model qwen3.6-27b
```

### 14.3 build-onpolicy

Build probes from both teacher and student prefixes.

Example:

```sh
cubesandbox-swe hint-eval build-onpolicy \
  --prefixes results/hint_eval_v2/prefixes.jsonl \
  --support results/hint_eval_v2/prefix_support.jsonl \
  --output results/hint_eval_v2/probes.jsonl \
  --max-candidates 4 \
  --hint-strength l2 \
  --seed 0
```

Each probe must include:

```json
{
  "schema_version": "hint_eval_probe_v2",
  "prefix_source": "teacher_success|student_onpolicy",
  "support_bucket": "high|medium|low|unknown",
  "oracle_source": "teacher_future|gold_patch|verifier|gpt_correction|student_success_future",
  "hints": {
    "neutral": "...",
    "causal": "...",
    "irrelevant": "...",
    "misleading": "..."
  }
}
```

### 14.4 compare-prefix-groups

Compare metrics by prefix group.

Example:

```sh
cubesandbox-swe hint-eval compare-prefix-groups \
  --scores results/hint_eval_v2/scores.qwen.jsonl \
  --online-results-glob 'results/**/*.json' \
  --output results/hint_eval_v2/prefix_group_comparison.json \
  --markdown results/hint_eval_v2/prefix_group_comparison.md
```

## 15. Scoring

Use the existing scorer path.

Example fake scorer:

```sh
cubesandbox-swe hint-eval score \
  --probes results/hint_eval_v2/probes.jsonl \
  --output results/hint_eval_v2/scores.fake.jsonl \
  --scorer fake \
  --model fake-model
```

Example Qwen scorer:

```sh
QWEN_BASE_URL=http://127.0.0.1:8000/v1 \
QWEN_API_KEY=dummy \
QWEN_MODEL=qwen3.6-27b \
cubesandbox-swe hint-eval score \
  --probes results/hint_eval_v2/probes.jsonl \
  --output results/hint_eval_v2/scores.qwen.jsonl \
  --scorer choice-logprobs \
  --base-url "$QWEN_BASE_URL" \
  --api-key-env QWEN_API_KEY \
  --model "$QWEN_MODEL"
```

## 16. Report structure

The report must contain:

1. Experiment setup
2. Prefix source counts
3. Support bucket counts
4. Hint source counts
5. Candidate quality summary
6. Offline metrics by prefix group
7. Online join coverage
8. Correlation with online outcome
9. Baseline comparison
10. Failure-mode examples
11. Valid and invalid conclusions

## 17. Conclusion gating

The report must clearly state the strongest supported conclusion.

### Level 0

Only fake scorer or fixture data.

Allowed claim:

```text
Pipeline works; no model capability claim.
```

### Level 1

Real Qwen scores but no online results.

Allowed claim:

```text
Offline process sensitivity only.
```

### Level 2

One Qwen model with online results.

Allowed claim:

```text
Task-level prediction and failure diagnosis for this model.
```

Forbidden:

```text
model ranking
```

### Level 3

Multiple Qwen variants with online results.

Allowed claim:

```text
Model-level ranking evidence under this scaffold.
```

### Level 4

Multiple variants plus fresh/private tasks.

Allowed claim:

```text
Stronger evidence that the offline score is a proxy for SWE environment competence.
```

## 18. Tests

Add deterministic offline tests:

```text
tests/test_hint_eval_v2_prefix_collection.py
tests/test_hint_eval_v2_support.py
tests/test_hint_eval_v2_build_onpolicy.py
tests/test_hint_eval_v2_prefix_group_compare.py
tests/test_hint_eval_v2_conclusion_gating.py
```

Fixtures:

```text
tests/fixtures/hint_eval_v2/teacher_success_trajectory.json
tests/fixtures/hint_eval_v2/student_failed_trajectory.json
tests/fixtures/hint_eval_v2/student_success_trajectory.json
tests/fixtures/hint_eval_v2/online_result_resolved.json
tests/fixtures/hint_eval_v2/online_result_unresolved.json
```

Tests must not:

```text
start CubeSandbox
require Docker
call real model endpoints
write secrets
```

## 19. Smoke flow

Fixture-only smoke:

```sh
rm -rf results/hint_eval_v2/smoke

cubesandbox-swe hint-eval collect-prefixes \
  --teacher-trajectory-glob 'tests/fixtures/hint_eval_v2/teacher_*.json' \
  --student-trajectory-glob 'tests/fixtures/hint_eval_v2/student_*.json' \
  --online-results-glob 'tests/fixtures/hint_eval_v2/online_*.json' \
  --output results/hint_eval_v2/smoke/prefixes.jsonl \
  --max-prefixes-per-trajectory 4 \
  --seed 0

cubesandbox-swe hint-eval support \
  --prefixes results/hint_eval_v2/smoke/prefixes.jsonl \
  --output results/hint_eval_v2/smoke/prefix_support.jsonl \
  --student-model qwen3.6-27b

cubesandbox-swe hint-eval build-onpolicy \
  --prefixes results/hint_eval_v2/smoke/prefixes.jsonl \
  --support results/hint_eval_v2/smoke/prefix_support.jsonl \
  --output results/hint_eval_v2/smoke/probes.jsonl \
  --max-candidates 4 \
  --hint-strength l2 \
  --seed 0

cubesandbox-swe hint-eval score \
  --probes results/hint_eval_v2/smoke/probes.jsonl \
  --output results/hint_eval_v2/smoke/scores.fake.jsonl \
  --scorer fake \
  --model fake-model

cubesandbox-swe hint-eval analyze \
  --scores results/hint_eval_v2/smoke/scores.fake.jsonl \
  --online-results-glob 'tests/fixtures/hint_eval_v2/online_*.json' \
  --output results/hint_eval_v2/smoke/summary.fake.json

cubesandbox-swe hint-eval compare-prefix-groups \
  --scores results/hint_eval_v2/smoke/scores.fake.jsonl \
  --online-results-glob 'tests/fixtures/hint_eval_v2/online_*.json' \
  --output results/hint_eval_v2/smoke/prefix_group_comparison.fake.json \
  --markdown results/hint_eval_v2/smoke/prefix_group_comparison.fake.md

cubesandbox-swe hint-eval report \
  --summary results/hint_eval_v2/smoke/summary.fake.json \
  --scores results/hint_eval_v2/smoke/scores.fake.jsonl \
  --output results/hint_eval_v2/smoke/report.fake.md
```

## 20. Completion criteria

This phase is complete when:

1. Existing MVP commands still work.
2. New V2 commands work.
3. Fixture-only V2 smoke flow works end to end.
4. Teacher-prefix, student-prefix, and high-support teacher-prefix metrics are reported separately.
5. Reports clearly distinguish continuation competence from on-policy competence.
6. Correlation uses `Goodness = -B` or explicitly reports negative `B` correlation.
7. Tests pass offline.
8. No third-party code is modified.
9. No large runtime outputs or secrets are committed.
