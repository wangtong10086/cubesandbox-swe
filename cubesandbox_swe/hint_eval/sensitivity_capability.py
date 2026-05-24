"""Cross-run analysis of hint sensitivity versus SWE capability."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from .analysis import pairwise_ranking_accuracy
from .io import read_json, read_jsonl, write_json
from .metrics import kendall_tau, pearson, probe_metrics, spearman


@dataclass(frozen=True)
class RunSpec:
    key: str
    label: str
    model: str
    root: Path
    scores_path: Path
    summary_path: Path
    ablation_path: Path
    prefix_group_path: Path
    note: str = ""


def default_run_specs(base_dir: str | Path = ".") -> list[RunSpec]:
    base = Path(base_dir)
    qwen36_root = base / "results/hint_eval_full/qwen50_repair_erroronly_20260523T112659Z"
    affine_root = base / "results/hint_eval_full/affine50_20260523T174013Z"
    qwen32_root = base / "results/hint_eval_full/qwen32_20260524T063443Z"
    qwen36_scores = base / "results/hint_eval_full/qwen50_20260522T204228Z/scores.qwen.jsonl"
    return [
        RunSpec(
            key="qwen36_repaired",
            label="Qwen3.6 27B repaired",
            model="Qwen/Qwen3.6-27B",
            root=qwen36_root,
            scores_path=qwen36_scores,
            summary_path=qwen36_root / "summary.qwen.json",
            ablation_path=qwen36_root / "ablation.qwen.json",
            prefix_group_path=qwen36_root / "prefix_group_comparison.json",
            note="修复后 online 结果 + 原始 Qwen3.6 离线 scores。",
        ),
        RunSpec(
            key="affine",
            label="Affine",
            model="0xbidkslj2/Affine-5CFUPEUT5fkqai3SLXirnnU9Px4bdvhVNcaDAJAcvF74roRE",
            root=affine_root,
            scores_path=affine_root / "scores.affine.jsonl",
            summary_path=affine_root / "summary.affine.json",
            ablation_path=affine_root / "ablation.affine.json",
            prefix_group_path=affine_root / "prefix_group_comparison.json",
            note="Plan 1 on-policy prefix/probe 口径。",
        ),
        RunSpec(
            key="qwen32",
            label="Qwen3 32B",
            model="Qwen/Qwen3-32B",
            root=qwen32_root,
            scores_path=qwen32_root / "scores.qwen32.jsonl",
            summary_path=qwen32_root / "summary.qwen32.json",
            ablation_path=qwen32_root / "ablation.qwen32.json",
            prefix_group_path=qwen32_root / "prefix_group_comparison.json",
            note="Plan 1 on-policy prefix/probe 口径。",
        ),
    ]


def analyze_default_runs(
    *,
    base_dir: str | Path = ".",
    raw_task_limit: int = 30,
    raw_probe_limit: int = 30,
) -> dict[str, Any]:
    return analyze_runs(default_run_specs(base_dir), raw_task_limit=raw_task_limit, raw_probe_limit=raw_probe_limit)


def analyze_runs(
    specs: list[RunSpec],
    *,
    raw_task_limit: int = 30,
    raw_probe_limit: int = 30,
) -> dict[str, Any]:
    runs = [analyze_run(spec, raw_task_limit=raw_task_limit, raw_probe_limit=raw_probe_limit) for spec in specs]
    model_level_correlations = model_level_correlations_for(runs)
    return {
        "schema_version": "hint_sensitivity_capability_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "hypothesis": "通过测量加入 hint 后的变化可以估算模型能力",
        "runs": runs,
        "model_level_correlations": model_level_correlations,
        "task_level_correlations": {run["key"]: run["task_level_correlations"] for run in runs},
        "ablation_summary": {run["key"]: run["ablation_baselines"] for run in runs},
        "verdict": verdict_for(runs, model_level_correlations),
    }


def analyze_run(spec: RunSpec, *, raw_task_limit: int, raw_probe_limit: int) -> dict[str, Any]:
    online_rows = read_jsonl(spec.root / "online_results.jsonl")
    score_records = read_jsonl(spec.scores_path)
    summary = read_json(spec.summary_path)
    ablation = read_json(spec.ablation_path)
    prefix_group = read_json(spec.prefix_group_path) if spec.prefix_group_path.exists() else {"groups": []}

    metric_rows = [probe_metrics(record) for record in score_records]
    online_task_scores = online_score_by_key(online_rows)
    task_rows = task_metric_rows(metric_rows, online_task_scores)
    task_level_correlations = task_correlations(task_rows)
    status_counts = Counter(str(row.get("status") or "unknown") for row in online_rows)
    numeric_scores = [numeric_online_score(row) for row in online_rows]
    valid_scores = [score for score in numeric_scores if score is not None]
    aggregate = summary.get("aggregate") or aggregate_from_metric_rows(metric_rows)
    ablation_baselines = normalize_ablation_baselines(ablation)
    probe_rows = probe_sample_rows(metric_rows, online_task_scores, limit=raw_probe_limit)

    return {
        "key": spec.key,
        "label": spec.label,
        "model": spec.model,
        "note": spec.note,
        "root": str(spec.root),
        "scores_path": str(spec.scores_path),
        "summary_path": str(spec.summary_path),
        "ablation_path": str(spec.ablation_path),
        "prefix_group_path": str(spec.prefix_group_path),
        "online_rows": len(online_rows),
        "online_task_count": len(online_task_scores),
        "online_success_rate": mean(valid_scores) if valid_scores else None,
        "status_counts": dict(sorted(status_counts.items())),
        "ok_count": status_counts.get("ok", 0),
        "model_failure_count": status_counts.get("no_patch", 0)
        + status_counts.get("failed", 0)
        + status_counts.get("error", 0),
        "probe_count": len(metric_rows),
        "joined_count": ablation.get("joined_count"),
        "summary_joined_count": (summary.get("online") or {}).get("joined_probe_count"),
        "aggregate": {
            "L0": optional_float(aggregate.get("L0")),
            "L_plus": optional_float(aggregate.get("L_plus")),
            "G_plus": optional_float(aggregate.get("G_plus")),
            "mean_abs_G_plus": mean_abs(metric_rows, "G_plus"),
            "S_irrelevant": optional_float(aggregate.get("S_irrelevant")),
            "H_misleading": optional_float(aggregate.get("H_misleading")),
            "B": optional_float(aggregate.get("B")),
            "Goodness": optional_float(aggregate.get("Goodness")),
        },
        "ablation_baselines": ablation_baselines,
        "task_level_correlations": task_level_correlations,
        "prefix_groups": normalize_prefix_groups(prefix_group.get("groups") or []),
        "raw_task_rows": task_rows[:raw_task_limit],
        "raw_probe_rows_by_abs_hint_delta": probe_rows,
        "artifact_counts": {
            "online_results": len(online_rows),
            "scores": len(score_records),
            "summary_probe_count": summary.get("probe_count"),
            "summary_score_count": summary.get("score_count"),
            "ablation_joined_count": ablation.get("joined_count"),
        },
    }


def numeric_online_score(row: dict[str, Any]) -> float | None:
    value = row.get("score")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    status = row.get("status")
    if status == "ok":
        return 1.0
    if status in {"no_patch", "failed", "error"}:
        return 0.0
    return None


def online_key(row: dict[str, Any]) -> str | None:
    for key in (row.get("instance_id"), row.get("task_id")):
        if key is not None:
            return str(key)
    return None


def metric_key(row: dict[str, Any]) -> str | None:
    for key in (row.get("instance_id"), row.get("task_id")):
        if key is not None:
            return str(key)
    return None


def online_score_by_key(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = online_key(row)
        score = numeric_online_score(row)
        if key is None or score is None:
            continue
        grouped.setdefault(key, []).append({**row, "numeric_score": score})
    out = {}
    for key, values in grouped.items():
        statuses = Counter(str(row.get("status") or "unknown") for row in values)
        out[key] = {
            "online_score": mean(float(row["numeric_score"]) for row in values),
            "online_rows": len(values),
            "status_counts": dict(sorted(statuses.items())),
        }
    return out


def task_metric_rows(rows: list[dict[str, Any]], online_scores: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = metric_key(row)
        if key is None or key not in online_scores:
            continue
        grouped.setdefault(key, []).append(row)
    task_rows = []
    for key, group in grouped.items():
        first = group[0]
        task_rows.append(
            {
                "task_key": key,
                "task_id": first.get("task_id"),
                "instance_id": first.get("instance_id"),
                "probe_count": len(group),
                "online_score": online_scores[key]["online_score"],
                "online_rows": online_scores[key]["online_rows"],
                "status_counts": online_scores[key]["status_counts"],
                "L0": mean(float(row["L0"]) for row in group),
                "L_plus": mean(float(row["L_plus"]) for row in group),
                "G_plus": mean(float(row["G_plus"]) for row in group),
                "mean_abs_G_plus": mean(abs(float(row["G_plus"])) for row in group),
                "S_irrelevant": mean(float(row["S_irrelevant"]) for row in group),
                "H_misleading": mean(float(row["H_misleading"]) for row in group),
                "Goodness": mean(float(row["Goodness"]) for row in group),
            }
        )
    return sorted(task_rows, key=lambda row: (str(row.get("task_id")), str(row.get("instance_id"))))


def task_correlations(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    metrics = ["G_plus", "mean_abs_G_plus", "S_irrelevant", "H_misleading", "Goodness"]
    out = {}
    online = [float(row["online_score"]) for row in rows]
    for metric in metrics:
        values = [float(row[metric]) for row in rows]
        out[metric] = correlation_payload(values, online)
    return out


def correlation_payload(values: list[float], online: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "spearman": spearman(values, online),
        "kendall": kendall_tau(values, online),
        "pearson": pearson(values, online),
        "pairwise_accuracy": pairwise_ranking_accuracy(values, online),
    }


def model_level_correlations_for(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    online = [float(run["online_success_rate"]) for run in runs if run.get("online_success_rate") is not None]
    metrics = {
        "G_plus": [float(run["aggregate"]["G_plus"]) for run in runs],
        "mean_abs_G_plus": [float(run["aggregate"]["mean_abs_G_plus"]) for run in runs],
        "S_irrelevant": [float(run["aggregate"]["S_irrelevant"]) for run in runs],
        "H_misleading": [float(run["aggregate"]["H_misleading"]) for run in runs],
        "Goodness": [float(run["aggregate"]["Goodness"]) for run in runs],
        "probe_G_plus_spearman": [
            float(metric_lookup(run["ablation_baselines"], "G_plus").get("spearman") or 0.0) for run in runs
        ],
        "probe_Goodness_spearman": [
            float(metric_lookup(run["ablation_baselines"], "Goodness=-B").get("spearman") or 0.0) for run in runs
        ],
    }
    return {metric: correlation_payload(values, online) for metric, values in metrics.items()}


def verdict_for(runs: list[dict[str, Any]], correlations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    g_task = [run["task_level_correlations"]["G_plus"].get("spearman") for run in runs]
    g_probe = [metric_lookup(run["ablation_baselines"], "G_plus").get("spearman") for run in runs]
    signed_cross = correlations["G_plus"].get("spearman")
    abs_cross = correlations["mean_abs_G_plus"].get("spearman")
    return {
        "claim": "当前数据不足以验证该假设。",
        "rationale": [
            "跨模型只有 3 个点，只能作为描述性趋势，不能作为稳健统计证据。",
            f"signed G_plus 的跨模型 Spearman 为 {format_float(signed_cross)}，但 probe/task 级相关性不稳定。",
            f"mean_abs_G_plus 的跨模型 Spearman 为 {format_float(abs_cross)}，说明变化幅度本身不能直接解释能力。",
            "Goodness=-B 在不同模型上的方向也不一致，更适合作为诊断信号而不是能力估计器。",
        ],
        "probe_level_G_plus_spearman": g_probe,
        "task_level_G_plus_spearman": g_task,
    }


def normalize_ablation_baselines(ablation: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in ablation.get("baselines") or []:
        ci = ((row.get("bootstrap_ci") or {}).get("spearman") or {})
        rows.append(
            {
                "metric": row.get("metric"),
                "spearman": optional_float(row.get("spearman")),
                "kendall": optional_float(row.get("kendall")),
                "pearson": optional_float(row.get("pearson")),
                "pairwise_accuracy": optional_float(row.get("pairwise_accuracy")),
                "spearman_ci_low": optional_float(ci.get("low")),
                "spearman_ci_high": optional_float(ci.get("high")),
            }
        )
    return rows


def normalize_prefix_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in groups:
        correlations = row.get("correlations") if isinstance(row.get("correlations"), dict) else {}
        rows.append(
            {
                "prefix_group": row.get("prefix_group"),
                "probe_count": row.get("probe_count") or row.get("count"),
                "task_count": row.get("task_count"),
                "joined_count": row.get("joined_count") or correlations.get("joined_count"),
                "online_resolve_rate": optional_float(row.get("online_resolve_rate")),
                "L0": optional_float(row.get("L0")),
                "L_plus": optional_float(row.get("L_plus")),
                "G_plus": optional_float(row.get("G_plus")),
                "S_irrelevant": optional_float(row.get("S_irrelevant")),
                "H_misleading": optional_float(row.get("H_misleading")),
                "Goodness": optional_float(row.get("Goodness")),
                "spearman_goodness_online": optional_float(
                    row.get("spearman_goodness_online") or correlations.get("spearman_goodness_online")
                ),
                "pairwise_accuracy": optional_float(row.get("pairwise_accuracy") or correlations.get("pairwise_accuracy")),
            }
        )
    return rows


def probe_sample_rows(
    rows: list[dict[str, Any]],
    online_scores: dict[str, dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    enriched = []
    for row in rows:
        key = metric_key(row)
        enriched.append(
            {
                "probe_id": row.get("probe_id"),
                "task_id": row.get("task_id"),
                "instance_id": row.get("instance_id"),
                "prefix_group": row.get("prefix_group"),
                "cutpoint_type": row.get("cutpoint_type"),
                "L0": row.get("L0"),
                "L_plus": row.get("L_plus"),
                "G_plus": row.get("G_plus"),
                "abs_G_plus": abs(float(row.get("G_plus") or 0.0)),
                "S_irrelevant": row.get("S_irrelevant"),
                "Goodness": row.get("Goodness"),
                "online_score": (online_scores.get(key or "") or {}).get("online_score"),
            }
        )
    enriched.sort(key=lambda row: (-float(row["abs_G_plus"]), str(row.get("probe_id"))))
    return enriched[:limit]


def aggregate_from_metric_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    keys = ["L0", "L_plus", "G_plus", "S_irrelevant", "H_misleading", "B", "Goodness"]
    return {key: mean(float(row[key]) for row in rows) for key in keys} if rows else {key: 0.0 for key in keys}


def mean_abs(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return mean(abs(float(row[key])) for row in rows)


def metric_lookup(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    for row in rows:
        if row.get("metric") == metric:
            return row
    return {}


def optional_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def write_analysis_outputs(analysis: dict[str, Any], *, markdown_output: str | Path, json_output: str | Path) -> tuple[Path, Path]:
    markdown_path = Path(markdown_output)
    json_path = write_json(json_output, analysis)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_report(analysis), encoding="utf-8")
    return markdown_path, json_path


def render_report(analysis: dict[str, Any]) -> str:
    runs = analysis["runs"]
    lines = [
        "# Hint 敏感性能否估算模型能力：跨模型分析报告",
        "",
        f"Generated: `{analysis.get('generated_at')}`  ",
        f"Hypothesis: `{analysis.get('hypothesis')}`",
        "",
        "## 0. 结论",
        "",
        f"**结论：{analysis['verdict']['claim']}**",
        "",
    ]
    for item in analysis["verdict"]["rationale"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "这份报告把“hint 敏感性”拆成两个不同问题：",
            "",
            "- `G_plus=L0-L_plus`：正确 hint 是否让目标动作损失下降，属于有方向的 helpful hint effect。",
            "- `mean_abs_G_plus`：加入正确 hint 后变化幅度多大，只衡量敏感性强弱，不判断变化方向。",
            "",
            "如果要支持假设，需要看到 hint 变化与在线 SWE 能力在模型级、task 级、probe 级都稳定同向。",
            "",
            "## 1. 读者导览",
            "",
            "这份报告不是在比较模型 benchmark 排名，而是在检查一个更具体的方法论问题：",
            "",
            "> 如果一个模型在加入正确 hint 后的下一步动作概率变化很大，这个变化是否能用来估算它真实完成 SWE 任务的能力？",
            "",
            "报告里有两条证据线：",
            "",
            "- **在线能力线**：模型真的去修代码，CubeSandbox verifier 判断 patch 是否通过。",
            "- **离线 hint 线**：把模型运行过程切成若干中间状态，在这些状态上给/不给 hint，看模型对下一步动作的概率怎么变。",
            "",
            "最重要的阅读顺序：",
            "",
            "1. 先看“Model x Hint 敏感性矩阵”，理解三组模型的在线成功率和 hint 变化方向。",
            "2. 再看“跨模型相关性”，这是最直观但样本最少的证据。",
            "3. 然后看 probe 级和 task 级模型内证据，这是判断假设是否扎实的关键。",
            "4. 最后看 prefix group 和 raw rows，用来定位结果来自哪些轨迹状态。",
            "",
            "## 2. 项目背景：这个实验在测什么",
            "",
            "本项目用 CubeSandbox 跑 SWE-INFINITE 风格的软件工程任务。每个任务对应一个真实代码仓库里的 issue 或失败测试。模型通过 Codex 工具链在隔离 sandbox 中读代码、运行命令、生成 patch；最后 verifier 在同一个任务环境里检查 patch 是否真的解决问题。",
            "",
            "因此，在线结果回答的是：",
            "",
            "- 模型有没有生成 patch？",
            "- patch 能不能通过 verifier？",
            "- 多次重复运行里有多少次成功？",
            "",
            "但在线 SWE rollout 成本高、噪声大，而且失败原因复杂。Hint-Eval 的目标是构造一种更便宜的离线过程指标：从模型或 teacher 的轨迹中截取一个中间状态，再问模型“下一步该做什么”。如果这个离线指标能预测在线 verifier 成功率，就可以作为能力估计或早期诊断工具。",
            "",
            "```mermaid",
            "flowchart LR",
            "    A[真实 SWE task] --> B[模型在 CubeSandbox 中 rollout]",
            "    B --> C[生成 patch 或失败]",
            "    C --> D[verifier 给 online score]",
            "    B --> E[截取 trajectory prefix]",
            "    E --> F[构造下一步动作候选]",
            "    F --> G[neutral / causal / irrelevant / misleading hints]",
            "    G --> H[用模型 logprob 评分]",
            "    H --> I[计算 hint 敏感性指标]",
            "    D --> J[比较离线指标和在线能力]",
            "    I --> J",
            "```",
            "",
            "## 3. 关键概念",
            "",
            "| 概念 | 解释 |",
            "| --- | --- |",
            "| `task` | 一个 SWE-INFINITE 软件工程修复任务，通常来自真实仓库 issue 或测试失败。 |",
            "| `rollout` | 模型对一个 task 的一次完整尝试：读代码、运行命令、生成 patch、提交验证。 |",
            "| `online score` | verifier 给 rollout 的结果；本报告中 `ok=1`，模型失败为 `0`。 |",
            "| `trajectory` | rollout 过程中留下的操作序列，例如读文件、搜索、编辑、验证。 |",
            "| `prefix` | trajectory 的一个中间截断状态，表示“模型已经走到这里”。 |",
            "| `probe` | 在某个 prefix 上构造的离线选择题：给若干下一步候选动作，让模型打分。 |",
            "| `hint` | probe 中额外给模型的提示。包括 neutral、正确 causal、无关 irrelevant、误导 misleading。 |",
            "| `prefix group` | prefix 的来源分组，例如 teacher 成功轨迹、student 成功轨迹、student 失败轨迹。 |",
            "",
            "本报告关心的不是 hint 文案本身是否好看，而是 hint 加入前后模型对目标动作的概率分布是否发生变化，以及这种变化是否与真实 SWE 成功率有关。",
            "",
            "## 4. 数据来源与实验口径",
            "",
            "主分析使用三组完整实验。每组实验都围绕同一批 50 个任务，每个任务 4 次 rollout，目标是 200 条在线结果。",
            "",
            "| 模型 | 为什么纳入 | 主要口径 |",
            "| --- | --- | --- |",
            "| Qwen3.6 27B repaired | 最早完整跑通并修复过环境失败的一组 Qwen 结果。 | online 使用修复重跑结果；offline scores 复用原始 Qwen3.6。 |",
            "| Affine | 用同一套 Plan 1 on-policy 实验设置替换 student model。 | 重新收集 Affine 自己的 student trajectories/prefixes/probes。 |",
            "| Qwen3 32B | 在 Affine 后用同一实验配置替换为 Qwen3 32B。 | 重新收集 Qwen3 32B 自己的 student trajectories/prefixes/probes。 |",
            "",
            "失败分类非常重要：`no_patch` 和 `failed` 都是模型能力失败，计为在线 0 分。非基础设施原因造成的 `error` 也按模型失败处理。只有认证、路由、Targon/CubeSandbox、verifier 崩溃、runner 异常这类平台问题才应被归为基础设施失败并重跑或排除。",
            "",
            "## 5. 分析过程",
            "",
            "分析分成四层，越往下越接近“这个指标真的能不能预测能力”的严谨检验：",
            "",
            "| 层级 | 做法 | 用途 | 主要风险 |",
            "| --- | --- | --- | --- |",
            "| 模型级 | 把每个模型聚合成一个成功率和一个平均 hint 指标。 | 看整体趋势是否符合直觉。 | 只有 3 个模型点，不能做强统计结论。 |",
            "| Probe 级 | 每个 probe 都 join 到该 task 的 online score，计算 Spearman/Kendall/Pearson/Pairwise。 | 检查离线排序指标是否能预测 online 分数。 | 同一 task 可产生多个 probes，样本并非完全独立。 |",
            "| Task 级 | 先把同一 task 的 probes 聚合，再与该 task 的平均 online score 比较。 | 降低重复 probes 对相关性的影响。 | 成功样本很少，二值分数稀疏。 |",
            "| Prefix group 级 | 分别看 teacher 成功状态、student 成功状态、student 失败状态。 | 判断结论是否依赖某类状态来源。 | 某些组样本很小，例如 student success。 |",
            "",
            "判定标准是保守的：只有当同一个 hint 敏感性指标在模型级、probe 级、task 级、prefix group 级都表现出稳定方向，才认为假设得到支持。当前结果没有达到这个标准。",
            "",
            "## 6. 如何读后面的数值",
            "",
            "| 读数 | 正确理解 | 常见误读 |",
            "| --- | --- | --- |",
            "| `G_plus > 0` | 正确 hint 让目标动作损失下降，模型更偏向目标动作。 | 不能直接等价于模型更强；也可能表示模型依赖 hint。 |",
            "| `G_plus < 0` | 加 hint 后目标动作损失反而上升。 | 不一定说明 hint 文案错，也可能是模型对上下文/模板不稳。 |",
            "| `mean_abs_G_plus` 大 | 加 hint 后变化幅度大。 | 幅度大不代表能力强，因为方向可能是负面的。 |",
            "| `Spearman` 接近 0 | 离线指标排序和在线成功排序关系弱。 | 不能只看单个高值，必须看跨层级是否稳定。 |",
            "| `Pairwise` 接近 0.5 | 排序接近随机。 | 当 online 成功极少时，pairwise 会很敏感。 |",
            "",
            "## 7. Model x Hint 敏感性矩阵",
            "",
        ]
    )
    lines.extend(model_matrix(runs))
    lines.extend(
        [
            "",
            "```mermaid",
            "xychart-beta",
            '    title "Online SWE Success Rate"',
            f"    x-axis {json_list([run['label'] for run in runs])}",
            '    y-axis "success rate (%)" 0 --> 6',
            f"    bar {json_numbers([pct_value(run['online_success_rate']) for run in runs])}",
            "```",
            "",
            "```mermaid",
            "xychart-beta",
            '    title "Signed Helpful Hint Effect: G_plus"',
            f"    x-axis {json_list([run['label'] for run in runs])}",
            '    y-axis "G_plus" -3 --> 1',
            f"    bar {json_numbers([run['aggregate']['G_plus'] for run in runs])}",
            "```",
            "",
            "```mermaid",
            "xychart-beta",
            '    title "Absolute Hint Sensitivity: mean_abs_G_plus"',
            f"    x-axis {json_list([run['label'] for run in runs])}",
            '    y-axis "mean |G_plus|" 0 --> 5',
            f"    bar {json_numbers([run['aggregate']['mean_abs_G_plus'] for run in runs])}",
            "```",
            "",
            "## 8. 跨模型相关性",
            "",
            "注意：这里 `n=3`，只能作为描述性趋势，不能当作显著性证明。",
            "这一层回答的是“更强的模型平均上是否也有更有利的 hint 响应”。它适合提出假设，但不能单独验证假设。",
            "",
        ]
    )
    lines.extend(cross_model_table(analysis["model_level_correlations"]))
    lines.extend(
        [
            "",
            "## 9. 模型内证据：probe 级消融",
            "",
            "probe 级结果直接复用每轮实验的 ablation：同一批 score records 上更换离线排序指标，再与 online score 做相关性。",
            "这里的 `G_plus` 行最直接对应“加入正确 hint 后的变化是否预测能力”。`Goodness=-B` 行对应现有综合指标是否比单项指标更有用。",
            "",
        ]
    )
    for run in runs:
        lines.extend([f"### {run['label']}", ""])
        lines.extend(ablation_table(run["ablation_baselines"]))
        lines.extend([""])
    lines.extend(
        [
            "```mermaid",
            "xychart-beta",
            '    title "Probe-level G_plus Spearman by Model"',
            f"    x-axis {json_list([run['label'] for run in runs])}",
            '    y-axis "Spearman" -0.3 --> 0.2',
            f"    bar {json_numbers([metric_lookup(run['ablation_baselines'], 'G_plus').get('spearman') for run in runs])}",
            "```",
            "",
            "## 10. 模型内证据：task 级聚合",
            "",
            "task 级先把同一个 task/instance 的 probes 聚合，再与该 task 的 4 次 rollout 平均分比较，降低重复 probe 的权重影响。",
            "这一层比 probe 级更接近“任务是否能解出来”，但样本量会变小，而且本批模型成功率都很低。",
            "",
        ]
    )
    lines.extend(task_correlation_table(runs))
    lines.extend(
        [
            "",
            "## 11. Prefix group 视角",
            "",
            "prefix group 区分状态来源：teacher 成功轨迹、模型自己成功轨迹、模型自己失败轨迹。若 hint 敏感性真的能估算能力，方向应在这些状态来源上相对一致。",
            "如果一个指标只在 teacher 成功状态上有效，而在 student 自己真实到达的失败状态上无效，那么它更像 teacher-state 诊断，不足以作为 online 能力估计器。",
            "",
        ]
    )
    lines.extend(prefix_group_table(runs))
    lines.extend(
        [
            "",
            "## 12. Rollout 原始结果",
            "",
            "这些饼图显示在线能力基线。三组模型成功率都很低，所以后续相关性是在极稀疏成功信号上计算的；这也是本报告保持谨慎结论的原因之一。",
            "",
        ]
    )
    for run in runs:
        lines.extend(rollout_pie(run))
    lines.extend(
        [
            "## 13. Raw task-level rows",
            "",
            "下表保留部分原始 task 聚合数据，便于复核异常点。`online_score` 是该 task 多次 rollout 的平均分。",
            "例如 `online_score=0.250` 表示该任务 4 次 rollout 中有 1 次成功；`online_score=1.000` 表示 4 次全部成功。",
            "",
        ]
    )
    lines.extend(raw_task_table(runs))
    lines.extend(
        [
            "",
            "## 14. 最大 hint 变化 probes",
            "",
            "这些 rows 按 `abs_G_plus` 从大到小截取，展示最受 causal hint 影响的 probe。",
            "这张表主要用于诊断：变化最大的 probe 往往能暴露模型对某些上下文或 hint 模板特别不稳定，但它们不一定代表更高能力。",
            "",
        ]
    )
    lines.extend(raw_probe_table(runs))
    lines.extend(
        [
            "",
            "## 15. 字段解释与方法",
            "",
            "| 字段 | 含义 |",
            "| --- | --- |",
            "| `online_success_rate` | 200 次 SWE rollout 的平均 verifier score；`ok=1`，`no_patch/failed/error=0`。 |",
            "| `L0` | neutral/no business hint 条件下目标动作交叉熵，越低表示模型本来更偏向目标动作。 |",
            "| `L_plus` | 加入正确 causal hint 后的目标动作交叉熵。 |",
            "| `G_plus` | `L0-L_plus`；正数表示正确 hint 降低损失，负数表示 hint 后反而更差。 |",
            "| `mean_abs_G_plus` | `abs(G_plus)` 的均值，只表示加入 hint 后变化幅度。 |",
            "| `S_irrelevant` | 无关 hint 相对 neutral 的扰动幅度。 |",
            "| `H_misleading` | 误导 hint 相对 neutral 的变化。 |",
            "| `Goodness=-B` | 现有 hint-invariant 综合指标，越高越好。 |",
            "| `Spearman` | 离线指标与 online score 的秩相关；接近 0 表示排序关系弱。 |",
            "| `Pairwise` | online score 不同的样本对中，离线排序方向正确的比例。 |",
            "",
            "Qwen3.6 repaired 的特殊口径：online 能力使用修复后目录的 `online_results.jsonl`，离线 scores 仍使用原始 Qwen3.6 的 `scores.qwen.jsonl`，这与修复报告一致。",
            "",
            "## 16. 主要限制",
            "",
            "- 模型级样本只有 3 个，跨模型 Spearman 只能说明排序现象，不能说明统计显著性。",
            "- 三组模型在线成功数分别只有 9、2、1，成功信号非常稀疏。",
            "- Affine 和 Qwen3 32B 使用各自 on-policy prefixes；这更严谨，但也意味着 probe 集合不完全相同。",
            "- Qwen3.6 repaired 使用修复后的 online 结果和旧 offline scores，这是为了保留修复前已完成的离线评分，同时使用更准确的在线失败分类。",
            "- Hint-Eval 只看候选动作集合上的下一步偏好，不能覆盖完整 SWE 解题过程中的长程规划、执行错误和 patch 质量。",
            "",
            "因此，本报告的稳健结论是：hint 变化可以帮助诊断模型行为，但当前证据不足以把它当成可靠的模型能力估计器。",
            "",
            "## 17. Artifact index",
            "",
        ]
    )
    lines.extend(artifact_table(runs))
    return "\n".join(lines).rstrip() + "\n"


def model_matrix(runs: list[dict[str, Any]]) -> list[str]:
    rows = []
    for run in runs:
        ablation_g = metric_lookup(run["ablation_baselines"], "G_plus")
        ablation_goodness = metric_lookup(run["ablation_baselines"], "Goodness=-B")
        rows.append(
            [
                run["label"],
                pct(run["online_success_rate"]),
                status_summary(run["status_counts"]),
                run["probe_count"],
                run["joined_count"],
                fmt(run["aggregate"]["L0"]),
                fmt(run["aggregate"]["L_plus"]),
                fmt(run["aggregate"]["G_plus"]),
                fmt(run["aggregate"]["mean_abs_G_plus"]),
                fmt(run["aggregate"]["S_irrelevant"]),
                fmt(run["aggregate"]["Goodness"]),
                fmt(ablation_g.get("spearman")),
                fmt(ablation_goodness.get("spearman")),
            ]
        )
    return markdown_table(
        [
            "Model",
            "Online success",
            "Statuses",
            "Probes",
            "Joined",
            "L0",
            "L+",
            "G+",
            "mean |G+|",
            "S_irrel",
            "Goodness",
            "G+ Spearman",
            "Goodness Spearman",
        ],
        rows,
    )


def cross_model_table(correlations: dict[str, dict[str, Any]]) -> list[str]:
    labels = {
        "G_plus": "signed causal hint effect",
        "mean_abs_G_plus": "absolute causal hint sensitivity",
        "S_irrelevant": "irrelevant hint sensitivity",
        "H_misleading": "misleading hint effect",
        "Goodness": "composite Goodness",
        "probe_G_plus_spearman": "per-model probe G+ correlation",
        "probe_Goodness_spearman": "per-model probe Goodness correlation",
    }
    rows = []
    for metric, label in labels.items():
        row = correlations.get(metric) or {}
        rows.append(
            [
                f"`{metric}`",
                label,
                row.get("n"),
                fmt(row.get("spearman")),
                fmt(row.get("pearson")),
                fmt(row.get("pairwise_accuracy")),
                evidence_note(metric, row),
            ]
        )
    return markdown_table(["Metric", "Meaning", "n", "Spearman", "Pearson", "Pairwise", "Interpretation"], rows)


def ablation_table(rows: list[dict[str, Any]]) -> list[str]:
    table_rows = []
    for row in rows:
        table_rows.append(
            [
                f"`{row.get('metric')}`",
                fmt(row.get("spearman")),
                ci(row.get("spearman_ci_low"), row.get("spearman_ci_high")),
                fmt(row.get("kendall")),
                fmt(row.get("pearson")),
                fmt(row.get("pairwise_accuracy")),
            ]
        )
    return markdown_table(["Metric", "Spearman", "95% CI", "Kendall", "Pearson", "Pairwise"], table_rows)


def task_correlation_table(runs: list[dict[str, Any]]) -> list[str]:
    rows = []
    for run in runs:
        for metric in ["G_plus", "mean_abs_G_plus", "S_irrelevant", "H_misleading", "Goodness"]:
            corr = run["task_level_correlations"].get(metric) or {}
            rows.append(
                [
                    run["label"],
                    f"`{metric}`",
                    corr.get("n"),
                    fmt(corr.get("spearman")),
                    fmt(corr.get("kendall")),
                    fmt(corr.get("pearson")),
                    fmt(corr.get("pairwise_accuracy")),
                ]
            )
    return markdown_table(["Model", "Task metric", "Tasks", "Spearman", "Kendall", "Pearson", "Pairwise"], rows)


def prefix_group_table(runs: list[dict[str, Any]]) -> list[str]:
    rows = []
    for run in runs:
        for group in run["prefix_groups"]:
            rows.append(
                [
                    run["label"],
                    f"`{group.get('prefix_group')}`",
                    group.get("probe_count"),
                    group.get("task_count"),
                    group.get("joined_count"),
                    pct(group.get("online_resolve_rate")),
                    fmt(group.get("L0")),
                    fmt(group.get("L_plus")),
                    fmt(group.get("G_plus")),
                    fmt(group.get("Goodness")),
                    fmt(group.get("spearman_goodness_online")),
                ]
            )
    return markdown_table(
        ["Model", "Prefix group", "Probes", "Tasks", "Joined", "Online solve", "L0", "L+", "G+", "Goodness", "Spearman"],
        rows,
    )


def rollout_pie(run: dict[str, Any]) -> list[str]:
    counts = run["status_counts"]
    lines = [
        "```mermaid",
        f"pie showData title {run['label']} rollout outcomes",
    ]
    for key, value in counts.items():
        lines.append(f'    "{key}" : {value}')
    lines.extend(["```", ""])
    return lines


def raw_task_table(runs: list[dict[str, Any]]) -> list[str]:
    rows = []
    for run in runs:
        for row in run["raw_task_rows"]:
            rows.append(
                [
                    run["label"],
                    row.get("task_id"),
                    row.get("instance_id"),
                    row.get("probe_count"),
                    fmt(row.get("online_score")),
                    status_summary(row.get("status_counts") or {}),
                    fmt(row.get("G_plus")),
                    fmt(row.get("mean_abs_G_plus")),
                    fmt(row.get("S_irrelevant")),
                    fmt(row.get("Goodness")),
                ]
            )
    return markdown_table(
        ["Model", "task_id", "instance_id", "Probes", "online_score", "Statuses", "G+", "mean |G+|", "S_irrel", "Goodness"],
        rows,
    )


def raw_probe_table(runs: list[dict[str, Any]]) -> list[str]:
    rows = []
    for run in runs:
        for row in run["raw_probe_rows_by_abs_hint_delta"]:
            rows.append(
                [
                    run["label"],
                    row.get("probe_id"),
                    row.get("task_id"),
                    f"`{row.get('prefix_group')}`",
                    row.get("cutpoint_type"),
                    fmt(row.get("online_score")),
                    fmt(row.get("L0")),
                    fmt(row.get("L_plus")),
                    fmt(row.get("G_plus")),
                    fmt(row.get("abs_G_plus")),
                    fmt(row.get("Goodness")),
                ]
            )
    return markdown_table(
        ["Model", "probe_id", "task_id", "Prefix group", "Cutpoint", "online", "L0", "L+", "G+", "|G+|", "Goodness"],
        rows,
    )


def artifact_table(runs: list[dict[str, Any]]) -> list[str]:
    rows = []
    for run in runs:
        rows.append([run["label"], "root", run["root"]])
        rows.append([run["label"], "scores", run["scores_path"]])
        rows.append([run["label"], "summary", run["summary_path"]])
        rows.append([run["label"], "ablation", run["ablation_path"]])
        rows.append([run["label"], "prefix groups", run["prefix_group_path"]])
    return markdown_table(["Model", "Artifact", "Path"], rows)


def markdown_table(headers: list[Any], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(markdown_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(markdown_cell(cell) for cell in row) + " |")
    return lines


def markdown_cell(value: Any) -> str:
    if value is None:
        return "NA"
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def status_summary(counts: dict[str, Any]) -> str:
    keys = ["ok", "no_patch", "failed", "error", "unknown"]
    parts = [f"{key}:{counts[key]}" for key in keys if counts.get(key)]
    return ", ".join(parts) if parts else "NA"


def evidence_note(metric: str, row: dict[str, Any]) -> str:
    spearman_value = row.get("spearman")
    if metric == "G_plus" and spearman_value is not None and spearman_value > 0:
        return "3-model descriptive trend only; not enough to validate."
    if metric == "mean_abs_G_plus":
        return "Measures magnitude, but direction can be opposite to capability."
    return "Descriptive only because n=3."


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def format_float(value: Any) -> str:
    return fmt(value)


def pct(value: Any) -> str:
    if value is None:
        return "NA"
    return f"{float(value) * 100:.1f}%"


def pct_value(value: Any) -> float:
    return 0.0 if value is None else float(value) * 100


def ci(low: Any, high: Any) -> str:
    if low is None or high is None:
        return "NA"
    return f"{fmt(low)}..{fmt(high)}"


def json_list(values: list[Any]) -> str:
    escaped = [str(value).replace('"', '\\"') for value in values]
    return "[" + ", ".join(f'"{value}"' for value in escaped) + "]"


def json_numbers(values: list[Any]) -> str:
    return "[" + ", ".join(fmt(value) if value is not None else "0" for value in values) + "]"
