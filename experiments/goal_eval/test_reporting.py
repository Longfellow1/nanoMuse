"""Check metric denominators and fail-closed behavior, independently of runtime execution."""

from __future__ import annotations

import copy
import json
import unittest

from reporting import BENCHMARK, score


class MetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures = json.loads(BENCHMARK.read_text())["cases"]
        self.rows = [
            {
                "case": f["id"],
                "verdict": f["expected_verdict"],
                "decision": f["expected_decision"],
                "submissions": f["expected_submissions"],
                "source_status": f["expected_source_status"],
                "actual_outcome": f["outcome_truth"],
            }
            for f in self.fixtures
        ]

    def test_baseline_and_evaluator_failures_have_gold_denominators(self) -> None:
        metrics = score(self.rows, self.fixtures)
        self.assertEqual(metrics["source_false_completion"], {"count": 7, "total": 8})
        self.assertEqual(metrics["eval_false_completion"], {"count": 0, "total": 8})
        self.assertEqual(metrics["unverified_source_done"], {"count": 8, "total": 11})
        self.assertEqual(metrics["verified_precision"], {"count": 3, "total": 3})

    def test_unknown_on_true_but_stale_outcome_is_not_false_completion(self) -> None:
        metrics = score(self.rows, self.fixtures)
        self.assertEqual(metrics["unknown_handling"], {"count": 2, "total": 2})
        self.assertEqual(metrics["scenario_assertions"], {"count": 12, "total": 12})

    def test_false_verification_is_detected_even_if_evaluator_changes_its_own_label(self) -> None:
        rows = copy.deepcopy(self.rows)
        next(r for r in rows if r["case"] == "all_skipped")["verdict"] = "verified"
        metrics = score(rows, self.fixtures)
        self.assertEqual(metrics["eval_false_completion"], {"count": 1, "total": 8})
        self.assertEqual(metrics["scenario_assertions"]["count"], 11)

    def test_stale_verified_breaches_evidence_policy_even_if_business_truth_is_true(self) -> None:
        rows = copy.deepcopy(self.rows)
        next(r for r in rows if r["case"] == "stale_evidence")["verdict"] = "verified"
        metrics = score(rows, self.fixtures)
        self.assertEqual(metrics["eval_false_completion"]["count"], 0)
        self.assertEqual(metrics["unsupported_verification"], {"count": 1, "total": 9})

    def test_an_always_unknown_evaluator_cannot_pass_positive_controls(self) -> None:
        rows = [{**r, "verdict": "unknown"} for r in self.rows]
        metrics = score(rows, self.fixtures)
        self.assertEqual(metrics["eval_false_completion"]["count"], 0)
        self.assertEqual(metrics["positive_controls"], {"count": 0, "total": 3})
        self.assertEqual(metrics["verified_precision"], {"count": 0, "total": 0})

    def test_missing_or_duplicate_case_cannot_shrink_denominator(self) -> None:
        for rows in (self.rows[:-1], self.rows + [self.rows[0]]):
            with self.assertRaises(ValueError):
                score(rows, self.fixtures)


if __name__ == "__main__":
    unittest.main()
