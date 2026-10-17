"""Runtime integration checks, separate from the stdlib-only evaluator tests.

Run: python -m unittest discover -s experiments/goal_eval -p test_local_harness.py -v
Requires the repository's normal Python dependencies, no online model or browser.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

from local_harness import OccurrenceHost, Trial, replay, run_suite
from prototype import Contract, Criterion, Evidence, Ledger, Run


class LocalRuntimeTests(unittest.TestCase):
    temp: ClassVar[tempfile.TemporaryDirectory[str]]
    output: ClassVar[Path]
    report: ClassVar[dict[str, Any]]
    cases: ClassVar[dict[str, dict[str, Any]]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp.name) / "run"
        cls.report = asyncio.run(run_suite(cls.output))
        cls.cases = {row["case"]: row for row in cls.report["cases"]}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_success_has_independently_verified_acceptance(self) -> None:
        row = self.cases["success"]
        self.assertEqual(
            (row["source_status"], row["verdict"], row["submissions"]), ("done", "verified", 1)
        )
        self.assertEqual(row["delta"], 2)
        self.assertEqual(row["decision"], "escalate")

    def test_all_skipped_reproduces_false_completion_in_real_agent_loop(self) -> None:
        row = self.cases["all_skipped"]
        self.assertEqual(
            (row["source_progress"], row["source_status"], row["submissions"]), ("2/2", "done", 0)
        )
        self.assertTrue(row["unsupported_source_done"])
        self.assertNotEqual(row["verdict"], "verified")

    def test_unavailable_checker_keeps_completion_unknown(self) -> None:
        row = self.cases["unsupported_done"]
        self.assertEqual((row["source_status"], row["verdict"]), ("done", "unknown"))
        self.assertIsNone(row["delta"])

    def test_three_runs_of_successful_busywork_trigger_replan(self) -> None:
        rows = json.loads((self.output / "busy_no_progress/continuity.json").read_text())
        self.assertEqual([r["result"]["delta"] for r in rows], [0, 0, 0])
        self.assertEqual([r["decision"][0] for r in rows], ["continue", "continue", "replan"])
        row = self.cases["busy_no_progress"]
        self.assertEqual(row["failed_actor_calls"], 0)
        self.assertEqual(row["submissions"], 0)

    def test_window_closed_invalidates_plan(self) -> None:
        row = self.cases["window_closed"]
        self.assertEqual(row["verdict"], "contradicted")
        self.assertEqual(row["criteria"]["window_open"], "not_met")
        self.assertEqual(row["failed_actor_calls"], 1)
        self.assertEqual(row["decision"], "replan")

    def test_denied_submit_never_reaches_service_but_goal_can_claim_done(self) -> None:
        row = self.cases["approval_denied"]
        self.assertEqual((row["submissions"], row["failed_actor_calls"]), (0, 1))
        self.assertEqual((row["source_status"], row["verdict"]), ("done", "partial"))
        audit = (self.output / "approval_denied/audit.jsonl").read_text()
        entries = [json.loads(line) for line in audit.splitlines()]
        self.assertTrue(
            any(e.get("tool") == "lab_submit" and e["decision"] == "deny" for e in entries)
        )

    def test_revoked_acceptance_is_a_regression(self) -> None:
        row = self.cases["revoked_acceptance"]
        self.assertEqual(row["verdict"], "contradicted")
        self.assertEqual(row["delta"], -1)

    def test_abrupt_worker_exit_and_naive_retry_duplicate_the_side_effect(self) -> None:
        row = self.cases["crash_naive_retry"]
        self.assertEqual((row["submissions"], row["duplicate_submissions"]), (2, 1))
        self.assertEqual(row["verdict"], "contradicted")
        self.assertEqual(row["without_uniqueness_constraint"], "verified")
        state = json.loads((self.output / "crash_naive_retry/run_state.json").read_text())
        self.assertEqual((state["state"], state["exitcode"]), ("interrupted", 73))

    def test_reconciliation_prevents_duplicate_submission(self) -> None:
        row = self.cases["crash_reconcile"]
        self.assertEqual((row["submissions"], row["duplicate_submissions"]), (1, 0))
        self.assertEqual(row["verdict"], "verified")
        reconciled = json.loads((self.output / "crash_reconcile/reconciliation.json").read_text())
        self.assertFalse(reconciled["session_saved_after_crash"])
        self.assertFalse((self.output / "crash_reconcile/session.json").read_text() == "")

    def test_malformed_report_is_rejected_even_when_actor_marks_goal_done(self) -> None:
        row = self.cases["malformed_artifact"]
        self.assertEqual((row["source_status"], row["verdict"]), ("done", "partial"))
        self.assertEqual((row["submissions"], row["failed_actor_calls"]), (0, 1))
        self.assertEqual(row["criteria"]["report_valid"], "not_met")
        self.assertFalse(row["actual_outcome"])
        artifact = self.output / "malformed_artifact/workspace/report.json"
        self.assertEqual(json.loads(artifact.read_text()), {})

    def test_stale_evidence_becomes_unknown_without_refreshing_or_losing_truth(self) -> None:
        path = self.output / "stale_evidence"
        row = self.cases["stale_evidence"]
        fresh = json.loads((path / "work.1.view.evaluation.json").read_text())
        stale = json.loads((path / "work.1.evaluation.json").read_text())
        fresh_snapshot = json.loads((path / "work.1.view.snapshot.json").read_text())
        stale_snapshot = json.loads((path / "work.1.snapshot.json").read_text())
        self.assertEqual(fresh["status"], "verified")
        self.assertEqual((row["verdict"], row["decision"]), ("unknown", "continue"))
        self.assertIsNone(row["delta"])
        self.assertEqual(set(stale["criteria"].values()), {"unknown"})
        self.assertEqual(len(stale["rejected"]), 4)
        self.assertEqual(fresh_snapshot["evidence"], stale_snapshot["evidence"])
        self.assertGreater(stale_snapshot["now"] - fresh_snapshot["now"], 60)
        self.assertTrue(row["actual_outcome"])
        calls = [json.loads(line) for line in (path / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(sum(call["tool"] == "lab_check" for call in calls), 2)

    def test_persistent_host_dedupes_duplicate_delivery_before_actor_or_service(self) -> None:
        row = self.cases["duplicate_trigger"]
        self.assertEqual(
            (row["verdict"], row["submissions"], row["actor_calls"]), ("verified", 1, 3)
        )
        self.assertEqual(row["duplicate_submissions"], 0)
        delivery = json.loads((self.output / "duplicate_trigger/delivery.json").read_text())
        self.assertEqual(
            (delivery["received"], delivery["executed"], delivery["suppressed"]), (2, 1, 1)
        )
        self.assertTrue(delivery["host_only"])
        self.assertTrue(delivery["persistent_reopen_between_deliveries"])
        with sqlite3.connect(self.output / "duplicate_trigger/occurrences.db") as conn:
            self.assertEqual(
                conn.execute("SELECT state FROM occurrences").fetchall(), [("completed",)]
            )

    def test_ambiguous_host_delivery_requires_reconciliation_before_retry(self) -> None:
        path = Path(self.temp.name) / "ambiguous-occurrences.db"
        executions = []

        async def ambiguous_action() -> None:
            executions.append("submitted")
            raise RuntimeError("reply lost after side effect")

        host = OccurrenceHost(path)
        try:
            with self.assertRaisesRegex(RuntimeError, "reply lost"):
                asyncio.run(host.deliver("one", ambiguous_action))
        finally:
            host.close()
        host = OccurrenceHost(path)
        try:
            with self.assertRaisesRegex(RuntimeError, "reconcile before retry"):
                asyncio.run(host.deliver("one", ambiguous_action))
        finally:
            host.close()
        self.assertEqual(executions, ["submitted"])

    def test_ledger_repeated_record_does_not_append_another_evaluation(self) -> None:
        path = self.output / "success"
        snapshot = json.loads((path / "work.1.snapshot.json").read_text())
        contract = Contract(
            snapshot["contract"]["goal_id"],
            snapshot["contract"]["revision"],
            tuple(
                Criterion(**{**criterion, "collectors": tuple(criterion["collectors"])})
                for criterion in snapshot["contract"]["criteria"]
            ),
        )
        ledger = Ledger(path / "evaluations.db")
        try:
            before = ledger.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
            result = ledger.record(
                contract,
                Run(**snapshot["run"]),
                [Evidence(**e) for e in snapshot["evidence"]],
                now=snapshot["now"],
                previous=snapshot["previous"],
            )
            self.assertEqual(result, replay(path / "work.1.snapshot.json"))
            self.assertEqual(
                ledger.conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0], before
            )
        finally:
            ledger.close()

    def test_missing_service_metric_is_unknown_instead_of_a_false_fact(self) -> None:
        trial = Trial(Path(self.temp.name) / "missing-metric", "http://127.0.0.1:1/missing")
        try:
            with patch("local_harness.request_local", return_value={"accepted": True}):
                result = asyncio.run(trial.measure(Run("missing"), 10000))["result"]
            self.assertEqual(result["criteria"]["accepted"], "met")
            self.assertEqual(result["criteria"]["window_open"], "unknown")
            self.assertEqual(result["criteria"]["single_submission"], "unknown")
            self.assertEqual(result["violated"], [])
            self.assertNotEqual(result["status"], "contradicted")
        finally:
            trial.close()

    def test_observed_behavior_matches_all_gold_expectations(self) -> None:
        for row in self.report["cases"]:
            with self.subTest(case=row["case"]):
                self.assertEqual(row["decision"], row["expected_decision"])
                self.assertEqual(row["submissions"], row["expected_submissions"])
                self.assertEqual(row["source_status"], row["expected_source_status"])
                self.assertEqual(row["actual_outcome"], row["outcome_truth"])

    def test_all_saved_evaluations_replay_without_running_agent_or_service(self) -> None:
        snapshots = list(self.output.glob("*/*.snapshot.json"))
        self.assertEqual(self.report["case_count"], 12)
        self.assertEqual(self.report["fixture_verdicts_matched"], 12)
        self.assertGreater(len(snapshots), self.report["case_count"])
        for snapshot in snapshots:
            with self.subTest(snapshot=snapshot):
                self.assertIn(
                    replay(snapshot)["status"], ("verified", "partial", "unknown", "contradicted")
                )

    def test_preserves_existing_run_evidence(self) -> None:
        with self.assertRaises(FileExistsError):
            asyncio.run(run_suite(self.output))


if __name__ == "__main__":
    unittest.main()
