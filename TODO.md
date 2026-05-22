
# TODO: Hint-Invariant SWE Offline Evaluation

## Progress Log

- 2026-05-22: Started MVP implementation in the integration layer. Added the
  `cubesandbox_swe/hint_eval/` package, fixture trajectory inputs, unit tests,
  and docs for the offline build/score/analyze/report flow. Validation is still
  in progress.
- 2026-05-22: Completed fixture-only MVP validation. `SKIP_DOCTOR=1
  PYTHON_BIN=.venv/bin/python bash scripts/check.sh` passes, all
  `hint-eval --help` paths work, the fixture build -> score(fake) -> analyze ->
  report flow writes ignored artifacts under `results/hint_eval/`, and both
  third-party checkouts remain clean.

## Objective

Build a minimal but rigorous offline Hint-Invariant Process Evaluation pipeline on top of the existing `cubesandbox-swe` project.

The pipeline should use existing SWE-INFINITE/CubeSandbox trajectories as teacher data, construct counterfactual hint probes, score student models such as Qwen3.6-27B through OpenAI-compatible logprob adapters, and produce aggregate metrics that can later be compared against online SWE verifier scores.

This is an evaluation/data pipeline, not a new online solver.

## Existing project context

The current repository already provides:

- CubeSandbox template preparation for `affinefoundation/swe_infinite_images`.
- SWE-INFINITE solve/verify orchestration through CubeSandbox.
- Codex runtime access through CubeSandbox-backed MCP tools.
- Trajectory and rollout-bucket artifact writing.
- `cubesandbox-swe solve`, `verify`, `collect swe50`, `templates`, `doctor`, and `artifacts` CLI paths.

Do not rewrite these flows. Extend them.

## Non-goals

- Do not modify `third_party/CubeSandbox`.
- Do not modify `third_party/affinetes`.
- Do not require Docker commands in the new pipeline.
- Do not change the online solve behavior unless absolutely necessary for reading existing artifacts.
- Do not run large-scale data collection as part of this implementation.
- Do not hardcode GPT or Qwen-specific endpoint assumptions.
- Do not commit large runtime outputs under `results/`, `swe-e2e-runs/`, or `logs/`.

## New CLI surface

Add a new top-level CLI namespace:

```sh
cubesandbox-swe hint-eval --help
cubesandbox-swe hint-eval build --help
cubesandbox-swe hint-eval score --help
cubesandbox-swe hint-eval analyze --help
cubesandbox-swe hint-eval report --help
````

The commands should be deterministic and testable.

### Command 1: build

Build probe datasets from existing teacher trajectories.

Example:

```sh
cubesandbox-swe hint-eval build \
  --trajectory-glob 'results/**/*.json' \
  --output results/hint_eval/probes.jsonl \
  --max-cutpoints-per-trajectory 4 \
  --max-candidates 8 \
  --hint-strength l2 \
  --seed 0
```

Expected output:

- JSONL probe file.
- Each line is one probe.
- Each probe contains prefix, candidate actions, target distribution, and four hint conditions.

### Command 2: score

Score candidate actions under no-hint, causal-hint, irrelevant-hint, and misleading-hint conditions.

Example with fake scorer for tests:

```sh
cubesandbox-swe hint-eval score \
  --probes results/hint_eval/probes.jsonl \
  --output results/hint_eval/scores.fake.jsonl \
  --scorer fake \
  --model fake-model
```

Example with Qwen endpoint:

```sh
QWEN_BASE_URL=http://127.0.0.1:8000/v1 \
QWEN_API_KEY=dummy \
QWEN_MODEL=qwen3.6-27b \
cubesandbox-swe hint-eval score \
  --probes results/hint_eval/probes.jsonl \
  --output results/hint_eval/scores.qwen.jsonl \
  --scorer choice-logprobs \
  --base-url "$QWEN_BASE_URL" \
  --api-key-env QWEN_API_KEY \
  --model "$QWEN_MODEL"
```

### Command 3: analyze

Aggregate probe-level scores into task-level and model-level metrics.

Example:

```sh
cubesandbox-swe hint-eval analyze \
  --scores results/hint_eval/scores.qwen.jsonl \
  --online-results-glob 'results/**/*.json' \
  --output results/hint_eval/summary.qwen.json
```

### Command 4: report

Generate a markdown report.

Example:

```sh
cubesandbox-swe hint-eval report \
  --summary results/hint_eval/summary.qwen.json \
  --scores results/hint_eval/scores.qwen.jsonl \
  --output results/hint_eval/report.qwen.md
```

## Package structure

Create a new package:

```text
cubesandbox_swe/hint_eval/
  __init__.py
  schemas.py
  io.py
  trajectory.py
  cutpoints.py
  candidates.py
  hints.py
  metrics.py
  scoring.py
  analysis.py
  report.py
  cli.py
```

Wire it into `cubesandbox_swe/cli.py` as `hint-eval`.

## Probe schema

Each probe JSONL line should have this rough shape:

```json
{
  "schema_version": "hint_eval_probe_v1",
  "probe_id": "string",
  "task_id": "string-or-int-or-null",
  "instance_id": "string-or-null",
  "repo": "string-or-null",
  "trajectory_file": "path",
  "attempt": 1,
  "cutpoint_index": 0,
  "cutpoint_type": "file_localization|function_localization|diagnosis|edit_decision|verification",
  "prefix_messages": [],
  "future_evidence_summary": "short text summary derived from future successful trajectory",
  "candidate_actions": [],
  "target_distribution": {},
  "hints": {
    "neutral": "Additional diagnostic note: No extra information is available.",
    "causal": "...",
    "irrelevant": "...",
    "misleading": "..."
  },
  "leakage_flags": [],
  "source": {
    "builder": "deterministic_v1",
    "hint_strength": "l1|l2|l3",
    "seed": 0
  }
}
```

Candidate action shape:

```json
{
  "id": "A",
  "kind": "file|operation|command",
  "label": "Inspect relevant file",
  "text": "inspect src/example.py",
  "command": "sed -n '1,200p' src/example.py",
  "file_path": "src/example.py",
  "operation": "inspect_file",
  "is_positive": true,
  "weight": 1.0,
  "source": "future_success_action|gold_patch_metadata|negative_distractor|generic_distractor"
}
```

Target distribution should be a map from candidate id to probability, for example:

```json
{
  "A": 0.5,
  "B": 0.5,
  "C": 0.0,
  "D": 0.0
}
```

## Cutpoint extraction

Implement deterministic heuristics first.

Supported cutpoint types:

1. `file_localization`

   - before the first future-relevant `cube_read_file` or command that inspects a source file.
2. `function_localization`

   - before a future action that narrows to a symbol/function, if detectable.
3. `diagnosis`

   - after an observed failing test/error output, before the next meaningful inspect/edit action.
4. `edit_decision`

   - immediately before `cube_apply_patch` or the first edit-like action.
5. `verification`

   - after patch creation, before verification/test/diff action.

It is acceptable for the first version to only reliably support `file_localization`, `edit_decision`, and `verification`, as long as the implementation records why a cutpoint was selected.

## Candidate action generation

Implement three candidate levels:

1. File-level candidates:

   - `inspect <path>`
   - best first target for stable evaluation.
2. Operation-level candidates:

   - `inspect_relevant_file`
   - `run_failing_test`
   - `search_symbol`
   - `edit_target_function`
   - `verify_patch`
3. Command-level candidates:

   - shell-like command strings derived from future actions.
   - keep this as optional/noisier.

Positive candidates:

- Future successful `cube_read_file` paths.
- Future successful `cube_run` commands that inspect files, search symbols, run tests, or verify fixes.
- Future `cube_apply_patch` summarized as an edit operation.
- If available, patch-touched files from the final fix patch.

Negative candidates:

- Plausible but unused files from the same trajectory context.
- Generic distractor operations such as inspecting README, running unrelated full test suite, or editing config files.
- Misleading candidate should be plausible, not obviously silly.

Do not use exact patch hunks as positive candidate text.

## Hint generation

Generate four hint conditions for every probe:

1. Neutral hint:

   - same approximate format every time.
   - should not provide task-specific information.

2. Causal hint:

   - derived from future successful behavior.
   - should point to the relevant file/module/operation.
   - should not directly include code patch hunks or exact final diff lines.

3. Irrelevant hint:

   - plausible but task-irrelevant.
   - should be similar length and tone to causal hint.

4. Misleading hint:

   - plausible but points to a negative candidate or wrong subsystem.
   - should be similar length and tone to causal hint.

Hint strength:

- `l1`: weak direction only.
- `l2`: module/file/operation-level hint.
- `l3`: near-oracle hint.
- Default should be `l2`.

Add leakage guard:

- flag hint if it contains exact patch text, diff markers, or long substrings from the final patch.
- do not fail build by default; record `leakage_flags`.
- add `--fail-on-leakage` option.

## Scoring

Implement a scorer abstraction:

```python
class ScoreClient:
    def score_candidates(self, prompt: str, candidates: list[CandidateAction]) -> dict[str, float]:
        ...
```

Required scorers:

1. `fake`

   - deterministic, no network.
   - used by unit tests and docs.

2. `choice-logprobs`

   - OpenAI-compatible chat-completions style scorer.
   - Prompt the model to choose among candidate ids A/B/C/D.
   - Use response logprobs/top_logprobs when available.
   - If provider does not return enough candidate label logprobs, fail with a clear error.

3. `vllm-prompt-logprobs`

   - optional if the endpoint supports prompt/completion logprob scoring.
   - Intended for scoring candidate action continuations more directly.
   - Implement only if it can be done robustly without breaking tests.

Scoring conditions:

- `neutral`
- `causal`
- `irrelevant`
- `misleading`

For every probe and condition, compute normalized candidate probabilities.

## Metrics

For each probe:

- `L0`: cross entropy between target distribution and neutral/no-hint probabilities.
- `L_plus`: cross entropy under causal hint.
- `L_irrelevant`: cross entropy under irrelevant hint.
- `L_misleading`: cross entropy under misleading hint.
- `G_plus = L0 - L_plus`
- `S_irrelevant = abs(L_irrelevant - L0)` or KL-style sensitivity if probabilities are available.
- `H_misleading = L_misleading - L0`
- `B = L0 + lambda * max(0, G_plus) + mu * S_irrelevant + nu * max(0, H_misleading)`

Defaults:

```text
lambda = 0.5
mu = 0.25
nu = 0.25
```

Expose these as CLI options in `analyze`.

Do not tune these on final test data. Document that they are defaults for MVP only.

## Analysis

The analyzer should support:

- per-probe metrics.
- per-task aggregation.
- per-model aggregation.
- optional join with online SWE verifier result JSON if available.
- Spearman correlation with online score if enough tasks/models exist.
- Kendall correlation if enough pairs exist.
- pairwise ranking accuracy where meaningful.

Avoid heavy dependencies. Implement rank correlations with the standard library if practical.

If online results are missing, produce a report that says correlation could not be computed and still summarizes offline metrics.

## Report

Generate a markdown report with:

- number of trajectories loaded.
- number of probes built.
- probe counts by cutpoint type.
- candidate counts by kind.
- leakage flag counts.
- model/scorer used.
- aggregate `L0`, `G_plus`, `S_irrelevant`, `H_misleading`, `B`.
- correlation with online result if available.
- top 10 best/worst probes by `B`.
- examples of one causal, irrelevant, and misleading hint.

## Tests

Add unit tests under `tests/`:

```text
tests/test_hint_eval_schemas.py
tests/test_hint_eval_cutpoints.py
tests/test_hint_eval_candidates.py
tests/test_hint_eval_hints.py
tests/test_hint_eval_metrics.py
tests/test_hint_eval_cli.py
```

Use small fixtures only:

```text
tests/fixtures/hint_eval/sample_trajectory_success.json
tests/fixtures/hint_eval/sample_online_result.json
```

Tests must not start CubeSandbox.
Tests must not call external model endpoints.
Tests must not require Docker.
Tests must pass with `SKIP_DOCTOR=1`.

Minimum test coverage behavior:

- build creates at least one probe from sample trajectory.
- generated probes have four hint conditions.
- positive target distribution sums to 1.0.
- leakage guard catches direct patch text.
- fake scorer produces deterministic scores.
- analyze produces summary JSON.
- report produces markdown.

## Documentation

Add:

```text
docs/hint-invariant-eval.md
```

The doc should explain:

- what this evaluator measures.
- how it differs from teacher imitation.
- how to build probes from GPT teacher trajectories.
- how to score Qwen3.6-27B through an OpenAI-compatible endpoint.
- how to compare against online verifier scores.
- what conclusions are valid and what conclusions are not valid.

Update README documentation links.

## Artifact policy

All generated experiment outputs should go under:

```text
results/hint_eval/
```

Do not commit large generated files.

If adding source-controlled examples, keep them tiny and place them under:

```text
artifacts/examples/
tests/fixtures/
```

If `artifacts/manifest.json` exists, update it only with small metadata, not full experiment outputs.

## Validation commands

Before considering the implementation done, run:

```sh
SKIP_DOCTOR=1 PYTHON_BIN=.venv/bin/python bash scripts/check.sh
```

Also run:

```sh
cubesandbox-swe hint-eval --help
cubesandbox-swe hint-eval build --help
cubesandbox-swe hint-eval score --help
cubesandbox-swe hint-eval analyze --help
cubesandbox-swe hint-eval report --help
```

Run a fixture-only smoke test:

```sh
cubesandbox-swe hint-eval build \
  --trajectory-glob 'tests/fixtures/hint_eval/sample_trajectory_success.json' \
  --output results/hint_eval/probes.fixture.jsonl \
  --max-cutpoints-per-trajectory 4 \
  --seed 0

cubesandbox-swe hint-eval score \
  --probes results/hint_eval/probes.fixture.jsonl \
  --output results/hint_eval/scores.fixture.fake.jsonl \
  --scorer fake \
  --model fake-model

cubesandbox-swe hint-eval analyze \
  --scores results/hint_eval/scores.fixture.fake.jsonl \
  --online-results-glob 'tests/fixtures/hint_eval/sample_online_result.json' \
  --output results/hint_eval/summary.fixture.json

cubesandbox-swe hint-eval report \
  --summary results/hint_eval/summary.fixture.json \
  --scores results/hint_eval/scores.fixture.fake.jsonl \
  --output results/hint_eval/report.fixture.md
```

Confirm that:

- the report exists.
- the summary JSON exists.
- the fake scores are deterministic.
- no secrets are written.
- `third_party/CubeSandbox` and `third_party/affinetes` remain clean.

## Completion criteria

This TODO is complete when:

1. New `hint-eval` CLI exists and is documented.
2. Fixture-only build/score/analyze/report flow works end to end.
3. Unit tests pass without starting CubeSandbox.
4. Runtime artifacts are kept out of Git.
5. No changes are made under `third_party/`.
6. The implementation can later be used on real GPT teacher trajectories and Qwen3.6-27B scoring endpoints without code changes.
