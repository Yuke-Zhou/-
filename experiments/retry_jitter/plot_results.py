"""Standalone scientific figures from saved CSVs, using optional Matplotlib."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path

from run_experiment import sha256_file, write_json

LABELS = {"no_jitter": "No jitter", "medium_independent": "Medium, independent",
          "large_independent": "Large, independent", "medium_shared": "Medium, shared"}
COLORS = {"no_jitter": "#617489", "medium_independent": "#007f86",
          "large_independent": "#cc7a23", "medium_shared": "#ad4f69"}


def plot(run, output=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise SystemExit("Plotting needs Python 3.10+ and: python -m pip install -r requirements-plots.txt") from error
    run = Path(run)
    output = Path(output) if output else run / "figures"
    if output.exists() and any(output.iterdir()):
        raise ValueError("Figure directory is not empty; use --output with a new directory")
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "svg.hashsalt": "retry_jitter_v1"})
    analysis = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
    config = json.loads((run / "effective_config.json").read_text(encoding="utf-8"))
    groups = {(r["load"], r["strategy"]): r for r in analysis["groups"]}
    strategies = ["no_jitter", "medium_independent", "large_independent"]
    produced = []

    def save(fig, name):
        fig.tight_layout()
        for extension in ("png", "svg"):
            path = output / (name + "." + extension)
            metadata = {"Date": None, "Creator": "retry_jitter"} if extension == "svg" else {"Software": "retry_jitter"}
            fig.savefig(str(path), dpi=160, metadata=metadata)
            produced.append(path)
        plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    for ax, load in zip(axes, ("low", "high")):
        values = [100 * groups[(load, s)]["mean_unfinished_rate"] for s in strategies]
        ax.bar(range(3), values, color=[COLORS[s] for s in strategies], width=0.65)
        ax.set_xticks(range(3), ["None", "Medium", "Large"])
        ax.set_title("{} load (N={})".format(load.capitalize(), config["loads"][load]))
        ax.set_xlabel("Independent jitter amplitude")
        ax.set_ylim(0, 105)
        ax.grid(axis="y", alpha=0.2)
        for x, value in enumerate(values):
            ax.text(x, value + 2, "{:.1f}%".format(value), ha="center")
    axes[0].set_ylabel("Unfinished clients at deadline (%)")
    fig.suptitle("Primary outcome: means over {} paired seeds".format(analysis["seed_count"]))
    save(fig, "01_unfinished_rate")

    fig, ax = plt.subplots(figsize=(8, 3.5))
    names = ["H1_high_improvement", "low_improvement", "H2_load_interaction"]
    for position, name in enumerate(names):
        r = analysis["contrasts"][name]
        point, lower, upper = [100 * r[k] for k in ("mean", "ci95_low", "ci95_high")]
        ax.hlines(position, lower, upper, color="#007f86", linewidth=3)
        ax.plot(point, position, "o", color="#007f86")
    ax.set_yticks(range(3), ["H1: high-load improvement", "Low-load improvement", "H2: high minus low"])
    ax.axvline(0, color="#777777", linestyle="--", linewidth=1)
    ax.set_xlabel("Reduction in unfinished rate (percentage points)")
    ax.set_title("Paired seed-level bootstrap: 95% percentile intervals")
    ax.invert_yaxis()
    save(fig, "02_paired_effects")

    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    specifications = [("mean_unfinished_rate", 100, "Unfinished (%)"),
                      ("mean_peak_retry_count", 1, "Peak retries / 100 ms"),
                      ("mean_queue_rejections", 1, "Queue-full rejections")]
    for ax, (metric, scale, title) in zip(axes, specifications):
        ss = ["medium_independent", "medium_shared"]
        values = [scale * groups[("high", s)][metric] for s in ss]
        ax.bar(range(2), values, color=[COLORS[s] for s in ss], width=0.6)
        ax.set_xticks(range(2), ["Independent", "Shared"])
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.2)
    fig.suptitle("Mechanism ablation: same marginal waits, different client correlation")
    save(fig, "03_mechanism")

    # First configured seed is selected in advance; do not cherry-pick a nice run.
    example_seed = config["seed_start"]
    example_strategies = ["no_jitter", "medium_independent", "medium_shared"]
    retries = {s: [] for s in example_strategies}
    success_times = {s: [] for s in example_strategies}
    with gzip.open(str(run / "attempts.csv.gz"), "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["seed"]) == example_seed and row["load"] == "high" and row["strategy"] in retries:
                if int(row["attempt"]) > 1:
                    retries[row["strategy"]].append(int(row["send_ns"]) / 1e9)
    with gzip.open(str(run / "clients.csv.gz"), "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["seed"]) == example_seed and row["load"] == "high" and row["strategy"] in success_times:
                if row["success_ns"]:
                    success_times[row["strategy"]].append(int(row["success_ns"]) / 1e9)
    with (run / "trials.csv").open(encoding="utf-8", newline="") as handle:
        recovery = next(int(r["recovery_ns"]) / 1e9 for r in csv.DictReader(handle)
                        if int(r["seed"]) == example_seed)
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    deadline = config["deadline_seconds"]
    bins = [i * config["peak_window_seconds"] for i in range(int(deadline / config["peak_window_seconds"]) + 1)]
    for strategy in example_strategies:
        times = sorted(success_times[strategy])
        x = [0] + times + [deadline]
        y = [0] + [100 * (i + 1) / config["loads"]["high"] for i in range(len(times))] + [100 * len(times) / config["loads"]["high"]]
        axes[0].step(x, y, where="post", label=LABELS[strategy], color=COLORS[strategy], linewidth=2)
        axes[1].hist(retries[strategy], bins=bins, histtype="step", label=LABELS[strategy],
                     color=COLORS[strategy], linewidth=1.3)
    for ax in axes:
        ax.axvline(recovery, color="#555555", linestyle="--", label="Service recovers")
        ax.set_xlim(0, deadline)
        ax.grid(alpha=0.2)
    axes[0].set_ylim(0, 105)
    axes[0].set_ylabel("Clients completed (%)")
    axes[0].legend(fontsize=9)
    axes[1].set_ylabel("Retries / fixed 100 ms bin")
    axes[1].set_xlabel("Simulated seconds since first request")
    fig.suptitle("Preselected example seed {} (visual bins; metric uses sliding windows)".format(example_seed))
    save(fig, "04_example_timeline")
    write_json(output / "figures_manifest.json", {
        "matplotlib_version": matplotlib.__version__, "example_seed": example_seed,
        "input_sha256": {name: sha256_file(run / name) for name in
                         ("analysis.json", "trials.csv", "clients.csv.gz", "attempts.csv.gz", "effective_config.json")},
        "figure_sha256": {p.name: sha256_file(p) for p in produced},
        "notes": "Mean bars are descriptive; paired confidence intervals are in figure 02 and analysis.json."})
    print("Created {} figures in {}".format(len(produced), output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plot(args.run, args.output)
