"""Sweep the size of a group sharing retry draws, using the original simulator."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import platform
import random
from dataclasses import replace
from pathlib import Path

from analysis import mean, quantile
from simulator import derived_seed, make_scenario, ns, simulate, validate_config


ROOT = Path(__file__).resolve().parent
SHARES = tuple(range(0, 101, 5))
PROFILE_NAMES = tuple(f"shared_{share:03d}" for share in SHARES) + ("shared_050_split",)
LAUNCH_MODES = ("synchronous", "staggered")
PRACTICAL_GAP = 0.05
BOOTSTRAP_SEED = 20261119


def trial_inputs(config, seed):
    scenario = make_scenario(config, seed)
    clients = list(range(config["loads"]["high"]))
    random.Random(derived_seed(seed, "partial_members")).shuffle(clients)
    launch_rng = random.Random(derived_seed(seed, "partial_launch"))
    launches = tuple(ns(launch_rng.random()) for _ in clients)
    group_draws = []
    for group in range(5):
        rng = random.Random(derived_seed(seed, f"partial_group:{group}"))
        group_draws.append(tuple(rng.random() for _ in range(config["attempt_limit"])))
    return scenario, clients, launches, tuple(group_draws)


def assign_groups(scenario, members, group_draws, sizes):
    draws = list(scenario.draws)
    start = 0
    for size, group_stream in zip(sizes, group_draws):
        for client in members[start:start + size]:
            draws[client] = group_stream
        start += size
    return replace(scenario, draws=tuple(draws))


def summarize(trials, repetitions):
    indexed = {}
    for row in trials:
        key = (row["seed"], row["launch"], row["profile"])
        if key in indexed:
            raise ValueError(f"Duplicate trial {key}")
        indexed[key] = row
    seeds = sorted({key[0] for key in indexed})
    profiles = [(mode, profile) for mode in LAUNCH_MODES for profile in PROFILE_NAMES]
    expected = {(seed, mode, profile) for seed in seeds for mode, profile in profiles}
    if not seeds or set(indexed) != expected:
        raise ValueError("Incomplete or unexpected paired trial grid")
    for seed in seeds:
        if len({indexed[(seed, *key)]["recovery_ns"] for key in profiles}) != 1:
            raise ValueError(f"Inconsistent recovery time for seed {seed}")

    def difference(first, second):
        return [indexed[(seed, *first)]["unfinished_rate"] - indexed[(seed, *second)]["unfinished_rate"]
                for seed in seeds]

    curve = [(mode, profile) for mode, profile in profiles
             if profile not in ("shared_000", "shared_050_split")]
    paired = {(mode, profile): difference((mode, profile), (mode, "shared_000"))
              for mode, profile in curve}
    main = paired["synchronous", "shared_050"]
    staggered = paired["staggered", "shared_050"]
    supplementary = {
        "main_50_points_share_minus_independent": main,
        "sync_minus_staggered_50_point_effect": [a - b for a, b in zip(main, staggered)],
        "sync_one_group_minus_five_groups": difference(
            ("synchronous", "shared_050"), ("synchronous", "shared_050_split")),
    }
    centers = {key: mean(values) for key, values in paired.items()}
    get_centered_values = [(values.__getitem__, centers[key]) for key, values in paired.items()]
    bootstrap = {key: [] for key in supplementary}
    max_deviations = []
    rng = random.Random(BOOTSTRAP_SEED)
    count = len(seeds)
    for _ in range(repetitions):
        indices = rng.choices(range(count), k=count)
        max_deviations.append(max(abs(sum(map(get, indices)) / count - center)
                                  for get, center in get_centered_values))
        for key, values in supplementary.items():
            bootstrap[key].append(sum(map(values.__getitem__, indices)) / count)
    half_width = quantile(sorted(max_deviations), 0.95)
    contrasts = {}
    for key, values in supplementary.items():
        samples = sorted(bootstrap[key])
        contrasts[key] = {"mean_delta": mean(values), "ci95_lower": quantile(samples, 0.025),
                          "ci95_upper": quantile(samples, 0.975)}

    groups = []
    for mode, profile in profiles:
        rows = [indexed[seed, mode, profile] for seed in seeds]
        share = rows[0]["shared_percent"]
        center = mean(difference((mode, profile), (mode, "shared_000")))
        covered = (mode, profile) in paired
        groups.append({
            "launch": mode, "profile": profile, "shared_percent": share,
            "groups": rows[0]["groups"],
            "mean_unfinished_rate": mean([r["unfinished_rate"] for r in rows]),
            "delta_vs_independent": center,
            "simultaneous_ci95_lower": center - half_width if covered else (0 if share == 0 else None),
            "simultaneous_ci95_upper": center + half_width if covered else (0 if share == 0 else None),
            "mean_peak_retry_count": mean([r["peak_retry_count"] for r in rows]),
            "mean_queue_rejections": mean([r["rejected_queue_full"] for r in rows]),
            "mean_total_attempts": mean([r["total_attempts"] for r in rows]),
            "t95_reached_trials": sum(not r["t95_censored"] for r in rows),
            "trials_with_unfinished": sum(r["unfinished_count"] > 0 for r in rows),
        })

    thresholds = {}
    for mode in LAUNCH_MODES:
        points = [r for r in groups if r["launch"] == mode and (mode, r["profile"]) in paired]

        def first_matching(predicate):
            return next((r["shared_percent"] for r in points if predicate(r)), None)

        thresholds[mode] = {
            "first_positive_point_estimate": first_matching(lambda r: r["delta_vs_independent"] > 0),
            "first_detected_positive_delta": first_matching(lambda r: r["simultaneous_ci95_lower"] > 0),
            "first_practical_point_estimate": first_matching(lambda r: r["delta_vs_independent"] >= PRACTICAL_GAP),
            "first_detected_practical_delta": first_matching(lambda r: r["simultaneous_ci95_lower"] > PRACTICAL_GAP),
        }
    return {"seed_count": count, "trial_count": len(trials), "bootstrap_repetitions": repetitions,
            "bootstrap_seed": BOOTSTRAP_SEED, "practical_gap": PRACTICAL_GAP,
            "simultaneous_half_width": half_width, "simultaneous_comparisons": len(paired),
            "thresholds": thresholds, "contrasts": contrasts, "groups": groups}


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def save_json(path, item):
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(output, seed_start, seeds, repetitions):
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    validate_config(config)
    if config["loads"]["high"] != 100 or config["recovery_min_seconds"] <= 1:
        raise ValueError("Profiles require 100 clients and recovery after the staggered launch window")
    output.mkdir(parents=True, exist_ok=False)
    protocol = ROOT / "EXPERIMENT_PLAN_PARTIAL_SHARING.md"
    (output / "protocol_snapshot.md").write_bytes(protocol.read_bytes())
    save_json(output / "effective_config.json", {
        "base_simulator_config": config, "seed_start": seed_start, "seed_count": seeds,
        "shares": SHARES, "split_group_sizes": [10] * 5, "stagger_max_seconds": 1,
        "jitter_amplitude": 0.5, "practical_gap": PRACTICAL_GAP,
        "bootstrap_repetitions": repetitions, "bootstrap_seed": BOOTSTRAP_SEED,
        "logged_example_seed": seed_start,
    })
    rows = []
    with (output / "scenarios.jsonl").open("w", encoding="utf-8") as scenarios, \
            gzip.open(output / "example_attempts.jsonl.gz", "wt", encoding="utf-8") as example:
        for index, seed in enumerate(range(seed_start, seed_start + seeds), start=1):
            scenario, members, launches, group_draws = trial_inputs(config, seed)
            scenarios.write(json.dumps({"seed": seed, "recovery_ns": scenario.recovery_ns,
                                        "tie_priorities": scenario.priorities, "shared_order": members,
                                        "staggered_initial_ns": launches,
                                        "group_draws_by_failure_number": group_draws}) + "\n")
            profiles = [(f"shared_{share:03d}", share, (share,) if share else ()) for share in SHARES]
            profiles.append(("shared_050_split", 50, (10,) * 5))
            for profile, share, sizes in profiles:
                assigned = assign_groups(scenario, members, group_draws, sizes)
                strategy = {"name": profile, "amplitude": 0.5, "shared": False}
                for mode, initial in (("synchronous", None), ("staggered", launches)):
                    trial = simulate(config, assigned, "high", strategy, initial_arrivals_ns=initial)
                    summary = {key: value for key, value in trial["summary"].items()
                               if key not in ("load", "strategy", "shared")}
                    rows.append({"launch": mode, "profile": profile, "shared_percent": share,
                                 "groups": "+".join(map(str, sizes)), **summary})
                    if seed == seed_start:
                        for attempt in trial["attempts"]:
                            example.write(json.dumps({"launch": mode, "profile": profile, **attempt}) + "\n")
            if index % 25 == 0 or index == seeds:
                print(f"Simulated {index}/{seeds} paired seeds ({len(rows)} trials)", flush=True)
    write_csv(output / "trials.csv", rows)
    result = summarize(rows, repetitions)
    save_json(output / "analysis.json", result)
    write_csv(output / "by_profile.csv", result["groups"])
    manifest_inputs = ("run_partial_sharing.py", "analysis.py", "simulator.py", "config.json",
                       "EXPERIMENT_PLAN_PARTIAL_SHARING.md")
    save_json(output / "manifest.json", {
        "python_version": platform.python_version(),
        "inputs_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in manifest_inputs},
        "outputs_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sorted(output.iterdir())},
    })
    print(json.dumps(result["thresholds"], indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed-start", default=20000, type=int)
    parser.add_argument("--seeds", default=200, type=int)
    parser.add_argument("--bootstrap", default=10000, type=int)
    args = parser.parse_args()
    if args.seeds < 1 or args.bootstrap < 1:
        parser.error("--seeds and --bootstrap must be positive")
    run(args.output, args.seed_start, args.seeds, args.bootstrap)
