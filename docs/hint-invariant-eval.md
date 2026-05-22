# Hint-Invariant SWE Offline Evaluation

The hint-invariant evaluator builds offline probes from successful
SWE-INFINITE teacher trajectories. Each probe asks a student model to choose
among plausible next actions under four conditions: neutral, causal hint,
irrelevant hint, and misleading hint.

This measures how a model's process choices respond to hints. It is not a new
online solver, and it does not replace the SWE verifier. The intended use is to
compare offline process metrics against later online verification scores.

## How It Differs From Teacher Imitation

Teacher imitation asks whether the student repeats the teacher's action.
Hint-invariant evaluation asks a narrower question: given the same trajectory
prefix and candidate actions, does a helpful hint improve the probability of
teacher-supported actions, and do irrelevant or misleading hints destabilize
the model?

The default MVP metrics are:

- `L0`: neutral cross entropy against the target distribution.
- `G_plus`: improvement from the causal hint.
- `S_irrelevant`: sensitivity to irrelevant hints.
- `H_misleading`: harm under misleading hints.
- `B`: weighted burden score using default MVP weights.

These defaults are not tuned on final test data.

## Build Probes

Use existing trajectory JSON files as teacher data:

```sh
cubesandbox-swe hint-eval build \
  --trajectory-glob 'results/**/*.json' \
  --output results/hint_eval/probes.jsonl \
  --max-cutpoints-per-trajectory 4 \
  --max-candidates 8 \
  --hint-strength l2 \
  --seed 0
```

The first implementation uses deterministic heuristics for file localization,
edit decision, and verification cutpoints. It records the cutpoint reason in
each probe's `source.cutpoint_reason` field.

## Score Qwen Through An OpenAI-Compatible Endpoint

The fake scorer is deterministic and should be used for tests:

```sh
cubesandbox-swe hint-eval score \
  --probes results/hint_eval/probes.jsonl \
  --output results/hint_eval/scores.fake.jsonl \
  --scorer fake \
  --model fake-model
```

For a Qwen endpoint that implements OpenAI-compatible chat completions with
token logprobs:

```sh
QWEN_BASE_URL=http://127.0.0.1:8000/v1
QWEN_API_KEY=dummy
QWEN_MODEL=qwen3.6-27b

cubesandbox-swe hint-eval score \
  --probes results/hint_eval/probes.jsonl \
  --output results/hint_eval/scores.qwen.jsonl \
  --scorer choice-logprobs \
  --base-url "$QWEN_BASE_URL" \
  --api-key-env QWEN_API_KEY \
  --model "$QWEN_MODEL"
```

If the provider does not return top logprobs for all candidate labels, scoring
fails with a clear error instead of silently fabricating probabilities.

## Analyze And Report

```sh
cubesandbox-swe hint-eval analyze \
  --scores results/hint_eval/scores.qwen.jsonl \
  --online-results-glob 'results/**/*.json' \
  --output results/hint_eval/summary.qwen.json

cubesandbox-swe hint-eval report \
  --summary results/hint_eval/summary.qwen.json \
  --scores results/hint_eval/scores.qwen.jsonl \
  --output results/hint_eval/report.qwen.md
```

If online verifier results are missing or too sparse, the summary and report
still include offline metrics and mark correlation as unavailable.

## Valid Conclusions

This evaluator can support claims about local process sensitivity on the
constructed candidate set. It can identify tasks where hints improve,
destabilize, or mislead candidate selection.

It cannot prove that a model will solve the original SWE task online. It also
cannot prove causal robustness beyond the trajectory distribution, candidate
construction heuristic, and scoring endpoint behavior used for the run.
