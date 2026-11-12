"""Finite FIFO service and client retries; simulated time uses integer ns.

Only the experiment runner can access recovery time. Retry decisions depend on
the failure number and a pre-generated random draw, never on future service state.
The module intentionally has no network calls, real sleeps, or third-party deps.
"""
from __future__ import annotations

import hashlib
import heapq
import math
import random
from collections import deque
from dataclasses import dataclass

NS = 1_000_000_000


def ns(seconds):
    return int(round(seconds * NS))


def seconds(ticks):
    return ticks / NS


def derived_seed(seed, label):
    digest = hashlib.sha256((str(seed) + ":" + label).encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")


def validate_config(config):
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported config schema")
    for key in ("seed_start", "seed_count", "bootstrap_repetitions", "bootstrap_seed",
                "waiting_slots", "attempt_limit"):
        if type(config[key]) is not int:
            raise ValueError(key + " must be an integer")
    if config["seed_count"] < 1 or config["bootstrap_repetitions"] < 1:
        raise ValueError("Need at least one seed and bootstrap resample")
    if config["waiting_slots"] < 0 or not 1 <= config["attempt_limit"] <= 100:
        raise ValueError("Invalid queue capacity or attempt limit")
    if set(config["loads"]) != {"low", "high"}:
        raise ValueError("Loads must contain low and high")
    if any(type(n) is not int or n < 1 for n in config["loads"].values()):
        raise ValueError("Client counts must be positive integers")
    for key in ("recovery_min_seconds", "recovery_max_seconds", "service_seconds",
                "failure_response_seconds", "deadline_seconds", "base_mean_wait_seconds",
                "max_mean_wait_seconds", "peak_window_seconds", "practical_improvement"):
        if not math.isfinite(config[key]) or config[key] < 0:
            raise ValueError(key + " must be finite and nonnegative")
    for key in ("service_seconds", "failure_response_seconds", "deadline_seconds",
                "base_mean_wait_seconds", "max_mean_wait_seconds", "peak_window_seconds"):
        if ns(config[key]) < 1:
            raise ValueError(key + " must be at least 1 ns")
    if config["recovery_min_seconds"] > config["recovery_max_seconds"]:
        raise ValueError("Invalid recovery interval")
    if config["max_mean_wait_seconds"] < config["base_mean_wait_seconds"]:
        raise ValueError("Mean wait cap cannot be less than base wait")
    if not 0 <= config["practical_improvement"] <= 1:
        raise ValueError("Practical improvement must be a proportion")
    strategies = config["strategies"] + [config["mechanism_strategy"]]
    expected = {"no_jitter", "medium_independent", "large_independent", "medium_shared"}
    if len(strategies) != 4 or {s["name"] for s in strategies} != expected:
        raise ValueError("The preregistered four strategies must be present")
    for strategy in strategies:
        if not 0 <= strategy["amplitude"] <= 1 or type(strategy["shared"]) is not bool:
            raise ValueError("Invalid jitter strategy")
    lookup = {s["name"]: (s["amplitude"], s["shared"]) for s in strategies}
    if lookup != {"no_jitter": (0, False), "medium_independent": (0.5, False),
                  "large_independent": (1, False), "medium_shared": (0.5, True)}:
        raise ValueError("Do not silently change preregistered strategy definitions")


@dataclass(frozen=True)
class Scenario:
    seed: int
    recovery_ns: int
    priorities: tuple
    draws: tuple

    def to_dict(self):
        return {"seed": self.seed, "recovery_ns": self.recovery_ns,
                "priorities": self.priorities, "draws": self.draws}


def make_scenario(config, seed):
    """The same scenario supports both loads and every jitter amplitude."""
    count = max(config["loads"].values())
    environment_rng = random.Random(derived_seed(seed, "environment"))
    recovery = ns(environment_rng.uniform(config["recovery_min_seconds"],
                                          config["recovery_max_seconds"]))
    priorities = list(range(count))
    random.Random(derived_seed(seed, "tie_priority")).shuffle(priorities)
    draws = []
    for client in range(count):
        rng = random.Random(derived_seed(seed, "client:" + str(client)))
        draws.append(tuple(rng.random() for _ in range(config["attempt_limit"])))
    return Scenario(seed, recovery, tuple(priorities), tuple(draws))


def peak_in_window(times, width_ns):
    """Maximum arrivals in any half-open window [t, t + width), not fixed bins."""
    times = sorted(times)
    left = peak = 0
    for right, time in enumerate(times):
        while time - times[left] >= width_ns:
            left += 1
        peak = max(peak, right - left + 1)
    return peak


def simulate(config, scenario, load, strategy, initial_arrivals_ns=None):
    """Run a trial. Custom initial arrivals are used only for boundary tests.

    Formal runner always leaves initial_arrivals_ns unset (all first requests=0).
    Return per-trial metrics plus full attempt and client records.
    """
    count = config["loads"][load]
    deadline = ns(config["deadline_seconds"])
    service = ns(config["service_seconds"])
    failure_response = ns(config["failure_response_seconds"])
    base = ns(config["base_mean_wait_seconds"])
    mean_cap = ns(config["max_mean_wait_seconds"])
    capacity = config["waiting_slots"]
    limit = config["attempt_limit"]
    arrivals = [0] * count if initial_arrivals_ns is None else list(initial_arrivals_ns)
    if len(arrivals) != count or any(t < 0 or t >= deadline for t in arrivals):
        raise ValueError("Invalid initial arrivals")
    if len(scenario.priorities) < count or len(scenario.draws) < count:
        raise ValueError("Scenario has too few clients")
    prefix = {"seed": scenario.seed, "load": load, "strategy": strategy["name"]}
    attempts = []
    successful = [None] * count
    attempt_counts = [0] * count
    states = ["ready"] * count
    events = []
    sequence = 0
    queue = deque()
    busy = None
    max_waiting = 0

    def schedule(time, priority, kind, client, attempt_index=-1):
        nonlocal sequence
        heapq.heappush(events, (time, priority, scenario.priorities[client],
                               sequence, kind, client, attempt_index))
        sequence += 1

    def start_service(index, now):
        nonlocal busy
        busy = index
        record = attempts[index]
        record["service_start_ns"] = now
        record["planned_finish_ns"] = now + service
        states[record["client"]] = "in_service"
        schedule(now + service, 0, "complete", record["client"], index)

    for client, arrival in enumerate(arrivals):
        schedule(arrival, 2, "attempt", client)

    while events:
        now, _, _, _, kind, client, index = heapq.heappop(events)
        if now > deadline:
            break
        if kind == "complete":
            assert busy == index
            attempts[index]["response_ns"] = now
            successful[client] = now
            states[client] = "success"
            busy = None
            if queue:
                start_service(queue.popleft(), now)
        elif kind == "failure":
            record = attempts[index]
            record["response_ns"] = now
            if attempt_counts[client] >= limit:
                states[client] = "attempts_exhausted"
                record["retry_decision"] = "attempts_exhausted"
                continue
            failure_number = attempt_counts[client]
            mean_wait = min(base * (1 << (failure_number - 1)), mean_cap)
            # Row zero is the common stream in the shared-jitter ablation.
            draw_client = 0 if strategy["shared"] else client
            draw = scenario.draws[draw_client][failure_number - 1]
            delay = int(round(mean_wait * (1 + strategy["amplitude"] * (2 * draw - 1))))
            next_time = now + delay
            record.update(mean_wait_ns=mean_wait, uniform_draw=draw, wait_ns=delay,
                          next_attempt_ns=next_time)
            if next_time < deadline:
                states[client] = "waiting_retry"
                record["retry_decision"] = "scheduled"
                schedule(next_time, 2, "attempt", client)
            else:
                states[client] = "retry_after_deadline"
                record["retry_decision"] = "deadline"
        else:
            if now >= deadline:
                continue
            assert successful[client] is None
            assert attempt_counts[client] < limit
            attempt_counts[client] += 1
            record = dict(prefix, client=client, attempt=attempt_counts[client],
                          send_ns=now, outcome="", response_ns=None,
                          service_start_ns=None, planned_finish_ns=None,
                          mean_wait_ns=None, uniform_draw=None, wait_ns=None,
                          next_attempt_ns=None, retry_decision="")
            index = len(attempts)
            attempts.append(record)
            if now < scenario.recovery_ns:
                record["outcome"] = "unavailable"
            elif busy is None:
                record["outcome"] = "accepted"
                start_service(index, now)
                continue
            elif len(queue) < capacity:
                record["outcome"] = "accepted"
                queue.append(index)
                states[client] = "queued"
                max_waiting = max(max_waiting, len(queue))
                continue
            else:
                record["outcome"] = "queue_full"
            states[client] = "awaiting_failure"
            schedule(now + failure_response, 1, "failure", client, index)

    success_times = sorted(t for t in successful if t is not None)
    successes = len(success_times)
    target95 = (95 * count + 99) // 100
    t95 = seconds(success_times[target95 - 1]) if successes >= target95 else None
    retry_times = [r["send_ns"] for r in attempts
                   if r["attempt"] > 1 and r["send_ns"] >= scenario.recovery_ns]
    peak = peak_in_window(retry_times, ns(config["peak_window_seconds"]))
    summary = dict(prefix, amplitude=strategy["amplitude"], shared=int(strategy["shared"]),
                   recovery_ns=scenario.recovery_ns, clients=count, success_count=successes,
                   unfinished_count=count - successes, unfinished_rate=(count - successes) / count,
                   total_attempts=len(attempts),
                   rejected_unavailable=sum(r["outcome"] == "unavailable" for r in attempts),
                   rejected_queue_full=sum(r["outcome"] == "queue_full" for r in attempts),
                   admitted_count=sum(r["outcome"] == "accepted" for r in attempts),
                   peak_retry_count=peak,
                   peak_retry_rate_per_second=peak / config["peak_window_seconds"],
                   t95_seconds=t95, t95_censored=int(t95 is None), max_waiting=max_waiting)
    clients = [dict(prefix, client=i, attempts=attempt_counts[i], success_ns=successful[i],
                    final_state=states[i]) for i in range(count)]
    assert summary["total_attempts"] == (summary["rejected_unavailable"] +
                                         summary["rejected_queue_full"] + summary["admitted_count"])
    assert max_waiting <= capacity
    return {"summary": summary, "attempts": attempts, "clients": clients}
