"""Run the frozen protocol and persist full evidence. Python 3.7+; stdlib only."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import platform
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from analysis import analyze, write_summary
from simulator import make_scenario, simulate, validate_config

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = ["simulator.py", "analysis.py", "run_experiment.py", "plot_results.py",
                "verify_run.py", "config.json", "EXPERIMENT_PLAN.md", "IMPLEMENTATION_NOTES.md"]
ATTEMPT_FIELDS = ["seed", "load", "strategy", "client", "attempt", "send_ns", "outcome", "response_ns",
                  "service_start_ns", "planned_finish_ns", "mean_wait_ns", "uniform_draw", "wait_ns",
                  "next_attempt_ns", "retry_decision"]
CLIENT_FIELDS = ["seed", "load", "strategy", "client", "attempts", "success_ns", "final_state"]


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def gzip_text(path):
    # Avoid recording clock time or pathname in the gzip header.
    with Path(path).open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                yield text


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def execute(config_path, output, seeds=None, bootstrap=None, quiet=False):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    original = json.loads(config_path.read_text(encoding="utf-8"))
    config = dict(original)
    if seeds is not None:
        config["seed_count"] = seeds
    if bootstrap is not None:
        config["bootstrap_repetitions"] = bootstrap
    validate_config(config)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Output is not empty; choose a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "effective_config.json", config)
    source_hashes = {name: sha256_file(ROOT / name) for name in SOURCE_FILES}
    started = datetime.now(timezone.utc).isoformat()
    frozen = {"created_at_utc": started, "config_source_sha256": sha256_file(config_path),
              "source_sha256": source_hashes, "effective_config": config,
              "requested_overrides": {"seeds": seeds, "bootstrap": bootstrap},
              "python": sys.version, "platform": platform.platform(),
              "time_unit": "integer nanoseconds; round-to-nearest at random delay generation"}
    # This file is written before any trial outcomes are inspected.
    write_json(output / "protocol_snapshot.json", frozen)
    (output / "EXPERIMENT_PLAN.md").write_bytes((ROOT / "EXPERIMENT_PLAN.md").read_bytes())
    (output / "IMPLEMENTATION_NOTES.md").write_bytes((ROOT / "IMPLEMENTATION_NOTES.md").read_bytes())
    start = time.perf_counter()
    rows = []
    with gzip_text(output / "attempts.csv.gz") as attempt_handle, \
            gzip_text(output / "clients.csv.gz") as client_handle, \
            gzip_text(output / "scenarios.jsonl.gz") as scenario_handle:
        attempt_writer = csv.DictWriter(attempt_handle, fieldnames=ATTEMPT_FIELDS)
        client_writer = csv.DictWriter(client_handle, fieldnames=CLIENT_FIELDS)
        attempt_writer.writeheader()
        client_writer.writeheader()
        for offset in range(config["seed_count"]):
            seed = config["seed_start"] + offset
            scenario = make_scenario(config, seed)
            scenario_handle.write(json.dumps(scenario.to_dict(), separators=(",", ":")) + "\n")
            for load in ("low", "high"):
                strategies = list(config["strategies"])
                if load == "high":
                    strategies.append(config["mechanism_strategy"])
                for strategy in strategies:
                    result = simulate(config, scenario, load, strategy)
                    rows.append(result["summary"])
                    attempt_writer.writerows(result["attempts"])
                    client_writer.writerows(result["clients"])
            if not quiet and ((offset + 1) % 25 == 0 or offset + 1 == config["seed_count"]):
                print("Simulated {}/{} seeds ({} trials)".format(offset + 1, config["seed_count"], len(rows)), flush=True)
    with (output / "trials.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    stats = analyze(rows, config["bootstrap_repetitions"], config["bootstrap_seed"],
                    config["practical_improvement"])
    write_json(output / "analysis.json", stats)
    with (output / "group_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(stats["groups"][0]))
        writer.writeheader()
        writer.writerows(stats["groups"])
    write_summary(output / "RESULTS_SUMMARY.md", stats)
    elapsed = time.perf_counter() - start
    data_files = sorted(p.name for p in output.iterdir() if p.is_file())
    manifest = dict(frozen, completed_at_utc=datetime.now(timezone.utc).isoformat(),
                    wall_seconds=elapsed, trial_count=len(rows),
                    artifacts_sha256={name: sha256_file(output / name) for name in data_files})
    write_json(output / "manifest.json", manifest)
    if not quiet:
        print("Complete: {} trials in {:.2f} wall-clock seconds".format(len(rows), elapsed))
        for name in ("H1_high_improvement", "H2_load_interaction"):
            item = stats["contrasts"][name]
            print("{}: {:.4f}, CI [{:.4f}, {:.4f}], {}".format(
                name, item["mean"], item["ci95_low"], item["ci95_high"], item["directional_verdict"]))
        print("Output:", output)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--seeds", type=int, help="Smoke override; recorded in effective config")
    parser.add_argument("--bootstrap", type=int, help="Smoke override; recorded in effective config")
    args = parser.parse_args()
    output = args.output or ROOT / "results" / datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S_UTC")
    try:
        execute(args.config, output, args.seeds, args.bootstrap)
    except (ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
