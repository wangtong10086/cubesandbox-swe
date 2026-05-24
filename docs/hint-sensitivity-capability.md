# Hint Sensitivity vs SWE Capability

This note documents the cross-model analysis used to check the hypothesis:

```text
Measuring how a model changes after adding hints can estimate model capability.
```

The current evidence does not validate that hypothesis. The signed causal hint
effect has a descriptive trend across the three completed model runs, but there
are only three model points and the within-model probe/task correlations are not
stable. Absolute hint sensitivity moves in the opposite direction: the weakest
model shows the largest average change after adding the causal hint.

## Data Scope

The main comparison uses the three complete runs with matching 50-task, 4-repeat
SWE rollout structure:

| Model | Experiment directory | Online result policy |
| --- | --- | --- |
| Qwen3.6 27B repaired | `results/hint_eval_full/qwen50_repair_erroronly_20260523T112659Z` | repaired online results, original Qwen3.6 offline scores |
| Affine | `results/hint_eval_full/affine50_20260523T174013Z` | Plan 1 on-policy |
| Qwen3 32B | `results/hint_eval_full/qwen32_20260524T063443Z` | Plan 1 on-policy |

Pilot, smoke, and early partial runs are excluded from the main claim because
they either mix fixture data, have too few real rollouts, or were affected by
runtime failures.

## Metrics

| Metric | Meaning |
| --- | --- |
| `online_success_rate` | Mean online verifier score across rollouts. `ok=1`; `no_patch`, `failed`, and model-caused `error` count as 0. |
| `L0` | Target-action cross entropy under the neutral/no-business-hint condition. Lower is better. |
| `L_plus` | Target-action cross entropy after adding the correct causal hint. Lower is better. |
| `G_plus` | `L0 - L_plus`; positive means the causal hint lowered loss. |
| `mean_abs_G_plus` | Mean absolute causal hint effect. This measures sensitivity magnitude, not whether the change helps. |
| `S_irrelevant` | Perturbation from irrelevant hints. Lower is more robust. |
| `H_misleading` | Effect from misleading hints. |
| `Goodness=-B` | Existing hint-invariant composite score, oriented higher-is-better. |

## Current Result Matrix

| Model | Online success | Statuses | Probes | Joined | `L0` | `L+` | `G+` | `mean_abs_G+` | `G+` Spearman | Goodness Spearman |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen3.6 27B repaired | 4.5% | ok:9, no_patch:176, failed:15 | 325 | 325 | 4.610 | 4.438 | 0.172 | 1.218 | -0.141 | -0.007 |
| Affine | 1.0% | ok:2, no_patch:188, failed:10 | 580 | 580 | 2.266 | 3.073 | -0.806 | 1.223 | -0.208 | 0.108 |
| Qwen3 32B | 0.5% | ok:1, no_patch:192, failed:7 | 755 | 755 | 3.729 | 6.255 | -2.527 | 3.522 | 0.008 | -0.103 |

Cross-model correlations are descriptive only because `n=3`:

| Metric vs success rate | Spearman | Pearson | Interpretation |
| --- | ---: | ---: | --- |
| `G_plus` | 1.000 | 0.844 | Descriptive trend only; insufficient to validate the hypothesis. |
| `mean_abs_G_plus` | -1.000 | -0.598 | Larger hint movement does not imply higher capability in this data. |
| `Goodness` | -0.500 | -0.590 | Composite score direction is not stable across models. |

## Reproduce

The report generator reads existing artifacts only. It does not call model
endpoints and does not run CubeSandbox:

```sh
.venv/bin/python scripts/analyze_hint_sensitivity_capability.py \
  --raw-task-limit 30 \
  --raw-probe-limit 30
```

Default outputs are intentionally under ignored `results/` paths:

```text
results/hint_eval_full/hint_sensitivity_capability_report.md
results/hint_eval_full/hint_sensitivity_capability_analysis.json
```

Commit the script and this documentation, not generated `results/` artifacts.

## Interpretation

The hypothesis would need evidence that hint-change metrics are stable predictors
at multiple levels:

```text
model-level trend
task-level correlation within each model
probe-level ablation within each model
consistent direction across prefix groups
```

The current data fails that bar. `G_plus` looks ordered across the three model
averages, but within-model `G_plus` correlations are weak or negative for Qwen3.6
and Affine and near zero for Qwen3 32B. This makes hint sensitivity useful as a
diagnostic signal, but not yet a reliable capability estimator.
