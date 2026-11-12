"""Paired trial-level analysis, independent of the simulator implementation."""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path


def mean(values):
    return sum(values) / len(values)


def quantile(ordered, probability):
    position = (len(ordered) - 1) * probability
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def read_trials(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def analyze(rows, repetitions=10000, seed=20261111, practical_improvement=0.05):
    if repetitions < 1 or not rows:
        raise ValueError("Empty trials or invalid bootstrap count")
    indexed = {}
    groups = defaultdict(list)
    for row in rows:
        key = (int(row["seed"]), row["load"], row["strategy"])
        if key in indexed:
            raise ValueError("Duplicate trial: " + str(key))
        indexed[key] = row
        groups[(row["load"], row["strategy"])].append(row)
    seeds = sorted({key[0] for key in indexed})
    required = [(load, strategy) for load in ("low", "high")
                for strategy in ("no_jitter", "medium_independent", "large_independent")]
    required.append(("high", "medium_shared"))
    if len(indexed) != len(seeds) * len(required):
        raise ValueError("Trial grid incomplete or contains unexpected strategies")
    for trial_seed in seeds:
        for load, strategy in required:
            if (trial_seed, load, strategy) not in indexed:
                raise ValueError("Missing paired trial")
        recoveries = {int(indexed[(trial_seed, load, strategy)]["recovery_ns"])
                      for load, strategy in required}
        if len(recoveries) != 1:
            raise ValueError("Strategies do not share recovery time")

    def difference(load, first, second, metric):
        return [float(indexed[(s, load, first)][metric]) -
                float(indexed[(s, load, second)][metric]) for s in seeds]

    high = difference("high", "no_jitter", "medium_independent", "unfinished_rate")
    low = difference("low", "no_jitter", "medium_independent", "unfinished_rate")
    differences = {
        "H1_high_improvement": high,
        "low_improvement": low,
        "H2_load_interaction": [h - l for h, l in zip(high, low)],
        "mechanism_unfinished": difference("high", "medium_shared", "medium_independent", "unfinished_rate"),
        "mechanism_peak": difference("high", "medium_shared", "medium_independent", "peak_retry_count"),
        "mechanism_queue_rejections": difference("high", "medium_shared", "medium_independent", "rejected_queue_full")
    }
    bootstrap = {name: [] for name in differences}
    rng = random.Random(seed)
    for _ in range(repetitions):
        indices = [rng.randrange(len(seeds)) for _ in seeds]
        for name, values in differences.items():
            bootstrap[name].append(sum(values[i] for i in indices) / len(indices))
    contrasts = {}
    for name, values in differences.items():
        samples = sorted(bootstrap[name])
        lower, upper = quantile(samples, 0.025), quantile(samples, 0.975)
        verdict = "supported" if lower > 0 else ("nonpositive_in_test" if upper <= 0 else "inconclusive")
        contrasts[name] = {"mean": mean(values), "ci95_low": lower, "ci95_high": upper,
                           "directional_verdict": verdict}
    contrasts["H1_high_improvement"]["meets_practical_point_threshold"] = mean(high) >= practical_improvement

    grouped = []
    for (load, strategy), items in sorted(groups.items()):
        reached = [float(r["t95_seconds"]) for r in items if r["t95_seconds"] not in (None, "")]
        grouped.append({"load": load, "strategy": strategy, "trials": len(items),
                        "mean_unfinished_rate": mean([float(r["unfinished_rate"]) for r in items]),
                        "mean_unfinished_count": mean([float(r["unfinished_count"]) for r in items]),
                        "mean_total_attempts": mean([float(r["total_attempts"]) for r in items]),
                        "mean_peak_retry_count": mean([float(r["peak_retry_count"]) for r in items]),
                        "mean_queue_rejections": mean([float(r["rejected_queue_full"]) for r in items]),
                        "t95_reached_trials": len(reached),
                        "t95_mean_among_reached_only": mean(reached) if reached else None})
    return {"seed_count": len(seeds), "trial_count": len(rows), "bootstrap_repetitions": repetitions,
            "bootstrap_seed": seed, "practical_improvement": practical_improvement,
            "interval_method": "paired percentile bootstrap over independent seeds",
            "groups": grouped, "contrasts": contrasts,
            "scope": "Synthetic finite-queue model; this is not evidence of Internet service performance."}


def write_summary(path, result):
    """A machine-produced data note, not a course report."""
    lines = ["**自动统计摘要：随机重试实验**", "",
             "原始数据来源：本目录的事件模拟日志。没有真实海温观测或生产服务测量。",
             "正式配置、协议和代码指纹见 manifest.json；不能把下列结果称为新算法证明。", "",
             "| 负载 | 策略 | 平均未完成率 | 平均请求次数 | 平均重试峰值/窗口 | T95达成轮数 |",
             "|---|---|---:|---:|---:|---:|"]
    for row in result["groups"]:
        lines.append("| {load} | {strategy} | {rate:.2%} | {requests:.2f} | {peak:.2f} | {reached}/{trials} |".format(
            load=row["load"], strategy=row["strategy"], rate=row["mean_unfinished_rate"],
            requests=row["mean_total_attempts"], peak=row["mean_peak_retry_count"],
            reached=row["t95_reached_trials"], trials=row["trials"]))
    lines += ["", "| 配对差：正数表示预设方向 | 均值 | 95%区间 | 方向判断 |",
              "|---|---:|---|---|"]
    for name, row in result["contrasts"].items():
        lines.append("| {} | {:.6f} | [{:.6f}, {:.6f}] | {} |".format(
            name, row["mean"], row["ci95_low"], row["ci95_high"], row["directional_verdict"]))
    lines += ["", "H1和H2是预先指定的主假设；大幅抖动结果属于探索对照。",
              "比例差使用0到1单位，乘100才是百分点。峰值以配置中的滑动窗口计数。",
              "T95未达成的试验没有被删除；达成轮数必须与延迟一起解释。",
              "区间跨零表示证据不足；nonpositive_in_test只描述本测试，不证明普遍等效。",
              "低负载若全部按时完成，存在指标地板效应，不应声称固定等待在所有期限下最优。",
              "所有客户端同步启动、拒绝请求的CPU开销忽略、队列容量固定，均限制外推范围。", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recompute statistics from saved trial rows")
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="Use a new file so the original manifest remains valid")
    args = parser.parse_args()
    config = json.loads((args.run / "effective_config.json").read_text(encoding="utf-8"))
    result = analyze(read_trials(args.run / "trials.csv"), config["bootstrap_repetitions"],
                     config["bootstrap_seed"], config["practical_improvement"])
    if args.output.exists():
        parser.error("Output exists; choose a new file")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
