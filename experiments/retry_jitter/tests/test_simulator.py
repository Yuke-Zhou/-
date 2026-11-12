import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import analyze
from simulator import NS, Scenario, make_scenario, ns, peak_in_window, simulate, validate_config

ROOT = Path(__file__).resolve().parents[1]


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

    def run_trial(self, clients=5, recovery=0, strategy=0, **changes):
        config = copy.deepcopy(self.config)
        config["loads"] = {"low": clients, "high": clients}
        config.update(changes)
        scenario = make_scenario(config, 42)
        scenario = Scenario(scenario.seed, ns(recovery), scenario.priorities, scenario.draws)
        result = simulate(config, scenario, "high", config["strategies"][strategy])
        return result, config, scenario

    def test_no_outage_sufficient_queue_has_analytic_completion_times(self):
        result, _, _ = self.run_trial(clients=10, waiting_slots=20)
        self.assertEqual(result["summary"]["success_count"], 10)
        self.assertEqual(result["summary"]["total_attempts"], 10)
        self.assertEqual(sorted(c["success_ns"] for c in result["clients"]),
                         [ns(0.05) * i for i in range(1, 11)])

    def test_permanent_outage_and_backoff_have_analytic_attempt_times(self):
        result, _, _ = self.run_trial(clients=1, recovery=100, deadline_seconds=5)
        self.assertEqual([r["send_ns"] for r in result["attempts"]],
                         [ns(t) for t in (0, 0.27, 0.79, 1.81, 3.83)])
        self.assertEqual(result["summary"]["unfinished_rate"], 1)
        self.assertIsNone(result["summary"]["t95_seconds"])

    def test_attempt_limit_includes_initial_request(self):
        result, _, _ = self.run_trial(clients=3, recovery=100, attempt_limit=3)
        self.assertEqual(result["summary"]["total_attempts"], 9)
        self.assertTrue(all(c["attempts"] == 3 and c["final_state"] == "attempts_exhausted"
                            for c in result["clients"]))

    def test_zero_waiting_slots_still_admits_one_worker(self):
        result, _, _ = self.run_trial(clients=3, waiting_slots=0, attempt_limit=1)
        self.assertEqual(result["summary"]["success_count"], 1)
        self.assertEqual(result["summary"]["rejected_queue_full"], 2)

    def test_queue_capacity_excludes_in_service_request(self):
        result, _, _ = self.run_trial(clients=4, waiting_slots=2, attempt_limit=1)
        self.assertEqual(result["summary"]["success_count"], 3)
        self.assertEqual(result["summary"]["max_waiting"], 2)
        self.assertEqual(result["summary"]["rejected_queue_full"], 1)

    def test_exact_deadline_completion_counts_as_success(self):
        result, _, _ = self.run_trial(clients=1, service_seconds=1, deadline_seconds=1)
        self.assertEqual(result["summary"]["success_count"], 1)
        result, _, _ = self.run_trial(clients=1, service_seconds=1, deadline_seconds=0.999)
        self.assertEqual(result["summary"]["success_count"], 0)
        self.assertIsNone(result["attempts"][0]["response_ns"])

    def test_completion_precedes_arrival_at_identical_time(self):
        _, config, scenario = self.run_trial(clients=2, waiting_slots=0, attempt_limit=1)
        result = simulate(config, scenario, "high", config["strategies"][0], [0, ns(0.05)])
        self.assertEqual(result["summary"]["success_count"], 2)

    def test_shared_stream_preserves_synchrony_but_independent_does_not(self):
        _, config, scenario = self.run_trial(clients=5, recovery=100, attempt_limit=3)
        shared = simulate(config, scenario, "high", config["mechanism_strategy"])
        independent = simulate(config, scenario, "high", config["strategies"][1])
        shared_times = {r["send_ns"] for r in shared["attempts"] if r["attempt"] == 2}
        independent_times = {r["send_ns"] for r in independent["attempts"] if r["attempt"] == 2}
        self.assertEqual(len(shared_times), 1)
        self.assertGreater(len(independent_times), 1)

    def test_reproducibility_includes_full_logs(self):
        _, config, scenario = self.run_trial(clients=20, recovery=2.5)
        first = simulate(config, scenario, "high", config["strategies"][2])
        # Regenerated scenario has the configured recovery, not this test's override.
        regenerated = make_scenario(config, scenario.seed)
        regenerated = Scenario(regenerated.seed, scenario.recovery_ns, regenerated.priorities, regenerated.draws)
        self.assertEqual(first, simulate(config, regenerated, "high", config["strategies"][2]))
        self.assertEqual(first, simulate(config, scenario, "high", config["strategies"][2]))

    def test_raw_record_conservation_and_serial_service(self):
        for seed in range(4):
            scenario = make_scenario(self.config, seed)
            for strategy in self.config["strategies"] + [self.config["mechanism_strategy"]]:
                result = simulate(self.config, scenario, "high", strategy)
                summary = result["summary"]
                self.assertEqual(summary["success_count"] + summary["unfinished_count"], 100)
                self.assertEqual(sum(c["attempts"] for c in result["clients"]), len(result["attempts"]))
                intervals = sorted((r["service_start_ns"], r["planned_finish_ns"])
                                   for r in result["attempts"] if r["service_start_ns"] is not None)
                self.assertTrue(all(a[1] <= b[0] for a, b in zip(intervals, intervals[1:])))
                self.assertTrue(all(r["send_ns"] < ns(12) for r in result["attempts"]))

    def test_peak_half_open_boundary(self):
        self.assertEqual(peak_in_window([0, ns(.099), ns(.1), ns(.199)], ns(.1)), 2)
        self.assertEqual(peak_in_window([], ns(.1)), 0)

    def test_config_validation(self):
        validate_config(self.config)
        self.config["failure_response_seconds"] = 0
        with self.assertRaises(ValueError):
            validate_config(self.config)


class AnalysisTests(unittest.TestCase):
    def rows(self):
        rows = []
        for seed in range(4):
            for load in ("low", "high"):
                for strategy in ("no_jitter", "medium_independent", "large_independent", "medium_shared"):
                    if load == "low" and strategy == "medium_shared":
                        continue
                    rate = .5 if load == "high" and strategy in ("no_jitter", "medium_shared") else 0
                    rows.append(dict(seed=seed, load=load, strategy=strategy, recovery_ns=ns(3),
                                     unfinished_rate=rate, unfinished_count=100 * rate,
                                     total_attempts=100, peak_retry_count=10, rejected_queue_full=5,
                                     t95_seconds=None if rate else 4))
        return rows

    def test_constant_paired_difference_has_known_interval(self):
        result = analyze(self.rows(), repetitions=100)
        self.assertEqual(result["contrasts"]["H1_high_improvement"]["mean"], .5)
        self.assertEqual(result["contrasts"]["H1_high_improvement"]["ci95_low"], .5)
        self.assertEqual(result["contrasts"]["H2_load_interaction"]["ci95_high"], .5)

    def test_missing_or_duplicate_pairs_are_rejected(self):
        rows = self.rows()
        with self.assertRaises(ValueError):
            analyze(rows[:-1], repetitions=10)
        with self.assertRaises(ValueError):
            analyze(rows + [rows[0]], repetitions=10)

    def test_mismatched_environment_is_rejected(self):
        rows = self.rows()
        rows[0]["recovery_ns"] = ns(2)
        with self.assertRaises(ValueError):
            analyze(rows, repetitions=10)


if __name__ == "__main__":
    unittest.main()
