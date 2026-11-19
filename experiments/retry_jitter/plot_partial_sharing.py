"""Render the shared-jitter threshold curves from saved analysis."""
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator, PercentFormatter


def plot(run, output):
    result = json.loads((run / "analysis.json").read_text(encoding="utf-8"))
    rows = result["groups"]
    fig, (full, detail) = plt.subplots(1, 2, figsize=(11.2, 4.5), layout="constrained")
    modes = [("synchronous", "#176C91", "Synchronous start"),
             ("staggered", "#B85424", "Staggered start")]
    practical = 100 * result["practical_gap"]
    for ax in (full, detail):
        for mode, color, label in modes:
            points = [row for row in rows if row["launch"] == mode and row["groups"] != "10+10+10+10+10"]
            points.sort(key=lambda row: row["shared_percent"])
            xs = [row["shared_percent"] for row in points]
            ys = [100 * row["delta_vs_independent"] for row in points]
            lower = [100 * row["simultaneous_ci95_lower"] for row in points]
            upper = [100 * row["simultaneous_ci95_upper"] for row in points]
            ax.fill_between(xs, lower, upper, color=color, alpha=0.14, linewidth=0)
            ax.plot(xs, ys, "o-", ms=3.7, lw=1.8, color=color, label=label)
        ax.axhline(0, color="#3F4750", linewidth=0.8)
        ax.axhline(practical, color="#5D646C", linestyle="--", linewidth=1.1,
                   label=f"{practical:g}-point reference")
        ax.grid(axis="y", alpha=0.23)
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_xlim(-1, 101)
        ax.set_xlabel("Clients in the same sharing group")
        ax.set_ylabel("Increase in unfinished rate (percentage points)")
        ax.xaxis.set_major_locator(MultipleLocator(20))
        ax.xaxis.set_major_formatter(PercentFormatter(100, decimals=0))
    full.set_title("Entire range")
    full.legend(loc="upper left", frameon=False, fontsize=9)
    detail.set_title("Detail near the reference line")
    detail.set_ylim(-4, 13)
    detail.yaxis.set_major_locator(MultipleLocator(2))
    fig.suptitle(f"Retry jitter sharing vs. independent draws\n"
                 f"{result['seed_count']} paired seeds per condition · Shading: simultaneous 95% intervals",
                 fontsize=13)
    output.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "svg"):
        fig.savefig(output / f"sharing_thresholds.{extension}", dpi=180)
    plt.close(fig)

    index = {(row["launch"], row["profile"]): row for row in rows}
    profiles = [
        (("synchronous", "shared_000"), "Independent\nSynchronous"),
        (("synchronous", "shared_050"), "1 × 50\nSynchronous"),
        (("synchronous", "shared_050_split"), "5 × 10\nSynchronous"),
        (("staggered", "shared_050"), "1 × 50\nStaggered"),
    ]
    selected = [index[key] for key, _ in profiles]
    names = [label for _, label in profiles]
    fig, axes = plt.subplots(1, 3, figsize=(11.2, 3.5), layout="constrained")
    measures = [("mean_unfinished_rate", 100, "Unfinished at 12 s (%)"),
                ("mean_peak_retry_count", 1, "Peak retries per 100 ms"),
                ("mean_queue_rejections", 1, "Queue-full rejections")]
    for ax, (key, scale, title) in zip(axes, measures):
        points = [scale * row[key] for row in selected]
        bars = ax.bar(names, points, color=["#6E7E88", "#176C91", "#499086", "#B85424"])
        ax.bar_label(bars, fmt="%.2f", padding=3, fontsize=9)
        ax.set_title(title)
        ax.set_ylim(0, max(points) * 1.22)
        ax.tick_params(axis="x", labelsize=8)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.2)
        ax.set_axisbelow(True)
    fig.suptitle(f"Sharing group size and first-request timing · Means over {result['seed_count']} seeds")
    for extension in ("png", "svg"):
        fig.savefig(output / f"sharing_mechanisms.{extension}", dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plot(args.run, args.output or args.run / "figures")
