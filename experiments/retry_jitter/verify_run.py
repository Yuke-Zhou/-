"""Verify hashes and independently recompute metrics from exported raw logs."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from run_experiment import ROOT, sha256_file
from simulator import NS, ns, peak_in_window


def require(condition, message):
    if not condition:
        raise ValueError(message)


def trial_key(row):
    return int(row["seed"]), row["load"], row["strategy"]


def verify(run, check_source=True):
    run = Path(run)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    config = json.loads((run / "effective_config.json").read_text(encoding="utf-8"))
    require(config == manifest["effective_config"], "Effective config differs from manifest")
    for name, digest in manifest["artifacts_sha256"].items():
        require(Path(name).name == name, "Invalid artifact filename")
        require(sha256_file(run / name) == digest, "Artifact checksum mismatch: " + name)
    if check_source:
        for name, digest in manifest["source_sha256"].items():
            require(sha256_file(ROOT / name) == digest, "Source changed after run: " + name)
    with (run / "trials.csv").open(encoding="utf-8", newline="") as handle:
        trial_rows = list(csv.DictReader(handle))
    trials = {trial_key(r): r for r in trial_rows}
    require(len(trials) == len(trial_rows) == manifest["trial_count"], "Trial count mismatch")
    clients = defaultdict(list)
    with gzip.open(str(run / "clients.csv.gz"), "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            clients[trial_key(row)].append(row)
    outcomes = defaultdict(Counter)
    retry_times = defaultdict(list)
    client_attempts = Counter()
    service_intervals = defaultdict(list)
    attempts_count = 0
    deadline = ns(config["deadline_seconds"])
    for_rows = gzip.open(str(run / "attempts.csv.gz"), "rt", encoding="utf-8", newline="")
    with for_rows as handle:
        for row in csv.DictReader(handle):
            attempts_count += 1
            key = trial_key(row)
            require(key in trials, "Unknown trial in raw attempts")
            client = int(row["client"])
            send = int(row["send_ns"])
            require(0 <= send < deadline, "Attempt sent outside deadline")
            require(0 <= client < int(trials[key]["clients"]), "Invalid client id")
            client_attempts[key + (client,)] += 1
            require(int(row["attempt"]) == client_attempts[key + (client,)], "Nonsequential attempt number")
            require(int(row["attempt"]) <= config["attempt_limit"], "Attempt limit exceeded")
            outcome = row["outcome"]
            require(outcome in {"accepted", "unavailable", "queue_full"}, "Invalid outcome")
            outcomes[key][outcome] += 1
            if int(row["attempt"]) > 1 and send >= int(trials[key]["recovery_ns"]):
                retry_times[key].append(send)
            if row["response_ns"]:
                response = int(row["response_ns"])
                require(send <= response <= deadline, "Invalid observed response time")
                if outcome != "accepted":
                    require(response == send + ns(config["failure_response_seconds"]), "Failure RTT mismatch")
            if outcome == "accepted":
                require(send >= int(trials[key]["recovery_ns"]), "Admission during outage")
                if row["service_start_ns"]:
                    start, finish = int(row["service_start_ns"]), int(row["planned_finish_ns"])
                    require(start >= send, "Service precedes request")
                    require(finish - start == ns(config["service_seconds"]), "Service duration mismatch")
                    service_intervals[key].append((start, finish))
                    if row["response_ns"]:
                        require(int(row["response_ns"]) == finish, "Success precedes completion")
            if row["wait_ns"]:
                require(row["response_ns"] != "", "Retry scheduled before response")
                require(int(row["next_attempt_ns"]) == int(row["response_ns"]) + int(row["wait_ns"]),
                        "Retry delay arithmetic mismatch")
    for key, trial in trials.items():
        records = clients[key]
        count = int(trial["clients"])
        require(len(records) == count, "Client count mismatch")
        require({int(r["client"]) for r in records} == set(range(count)), "Duplicate client record")
        success_times = sorted(int(r["success_ns"]) for r in records if r["success_ns"])
        require(all(0 <= t <= deadline for t in success_times), "Success outside deadline")
        require(len(success_times) == int(trial["success_count"]), "Success count mismatch")
        unfinished = count - len(success_times)
        require(unfinished == int(trial["unfinished_count"]), "Unfinished count mismatch")
        require(math.isclose(unfinished / count, float(trial["unfinished_rate"])), "Unfinished rate mismatch")
        for record in records:
            require(int(record["attempts"]) == client_attempts[key + (int(record["client"]),)],
                    "Client attempt count mismatch")
        require(sum(outcomes[key].values()) == int(trial["total_attempts"]), "Request total mismatch")
        for outcome, metric in (("accepted", "admitted_count"), ("unavailable", "rejected_unavailable"),
                                ("queue_full", "rejected_queue_full")):
            require(outcomes[key][outcome] == int(trial[metric]), "Outcome total mismatch")
        require(peak_in_window(retry_times[key], ns(config["peak_window_seconds"])) == int(trial["peak_retry_count"]),
                "Sliding-window peak mismatch")
        threshold = (95 * count + 99) // 100
        if len(success_times) >= threshold:
            require(int(trial["t95_censored"]) == 0, "Incorrect T95 censoring")
            require(math.isclose(success_times[threshold - 1] / NS, float(trial["t95_seconds"])), "T95 mismatch")
        else:
            require(trial["t95_seconds"] == "" and int(trial["t95_censored"]) == 1, "Missing T95 censoring")
        intervals = sorted(service_intervals[key])
        require(all(previous[1] <= current[0] for previous, current in zip(intervals, intervals[1:])),
                "Single worker processed overlapping requests")
    return {"verified_trials": len(trials), "verified_clients": sum(map(len, clients.values())),
            "verified_attempts": attempts_count, "artifact_checksums": "passed",
            "source_checksums": "passed" if check_source else "not_requested",
            "raw_log_metric_reconstruction": "passed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--skip-source", action="store_true", help="Only for archived data with intentionally changed code")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.run, not args.skip_source), indent=2))
    except ValueError as error:
        parser.error(str(error))
