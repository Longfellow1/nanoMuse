"""Local runtime experiment: scripted actor, real tools, independent local oracle.

Run with the repository's development environment. Writes only to --output, uses
loopback HTTP, and never initializes an online model or reads a personal config.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from prototype import (
    Contract,
    Criterion,
    Evidence,
    Ledger,
    Run,
    canonical,
    decide,
    evaluate,
    observation,
)

from nanomuse.agent import MuseAgent
from nanomuse.config import Settings
from nanomuse.goals import GoalStore
from nanomuse.llm import MockLLM
from nanomuse.schema import Function, LLMResponse, Message, RiskLevel, ToolCall, ToolResult
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.tools import Goals, Terminate, ToolCollection
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.files import Files
from nanomuse.ui import HeadlessUI

REPORT = {"title": "Weekly report", "items": ["A measured result"]}
NOW = 10000


def benchmark_cases() -> list[dict[str, Any]]:
    """The versioned benchmark owns expectations; runtime code owns fault injection."""
    data = json.loads(Path(__file__).with_name("benchmark.json").read_text())
    return data["cases"]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def valid_report(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("title"), str)
        and bool(value["title"].strip())
        and isinstance(value.get("items"), list)
        and bool(value["items"])
        and all(isinstance(x, str) and bool(x.strip()) for x in value["items"])
    )


def request_local(url: str, payload: Any = None) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("the experiment accepts loopback HTTP only")
    body = canonical(payload).encode() if payload is not None else None
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(req, timeout=2) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise ValueError("oracle response must be an object")
    return value


class World:
    """Controller-owned service state, outside the actor's file workspace.

    The submit endpoint deliberately lacks deduplication, to expose unsafe retries.
    Only the test controller can close/revoke/disable the service.
    """

    def __init__(self, path: Path):
        self.path = path
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE states (id TEXT PRIMARY KEY, window_open INTEGER, accepted INTEGER, "
                "submissions INTEGER, available INTEGER)"
            )
        world = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def do_GET(self) -> None:
                self.respond(False)

            def do_POST(self) -> None:
                self.respond(True)

            def respond(self, submit: bool) -> None:
                case = self.path.strip("/")
                with sqlite3.connect(world.path) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute("SELECT * FROM states WHERE id=?", (case,)).fetchone()
                    code = 200
                    data: Any
                    if row is None:
                        code, data = 404, {"error": "unknown_object"}
                    elif not row["available"]:
                        code, data = 503, {"error": "service_unavailable"}
                    elif submit and not row["window_open"]:
                        code, data = 409, {"error": "window_closed"}
                    elif submit:
                        try:
                            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                        except (ValueError, TypeError):
                            data = None
                        if not valid_report(data):
                            code, data = 422, {"error": "invalid_report"}
                        else:
                            conn.execute(
                                "UPDATE states SET accepted=1, submissions=submissions+1 WHERE id=?",
                                (case,),
                            )
                            data = {"accepted": True}
                    else:
                        data = {
                            "accepted": bool(row["accepted"]),
                            "window_open": bool(row["window_open"]),
                            "submissions": row["submissions"],
                            "single_submission": row["submissions"] <= 1,
                        }
                body = canonical(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def configure(self, case: str, *, available: bool = True, window_open: bool = True) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO states VALUES (?,?,0,0,?)", (case, window_open, available))

    def revoke(self, case: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE states SET accepted=0 WHERE id=?", (case,))

    def truth(self, case: str) -> dict[str, Any]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM states WHERE id=?", (case,)).fetchone()
            assert row is not None
            return dict(row)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class OccurrenceHost:
    """Experimental host dedupe, deliberately outside the nanoMuse scheduler.

    Completed occurrences survive a host restart. An ambiguous started occurrence
    requires reconciliation instead of blind retry; this is not an exactly-once
    transaction across the service and host database.
    """

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS occurrences (id TEXT PRIMARY KEY, state TEXT)"
        )
        self.conn.commit()

    async def deliver(self, occurrence_id: str, action: Callable[[], Awaitable[Any]]) -> bool:
        with self.conn:
            row = self.conn.execute(
                "SELECT state FROM occurrences WHERE id=?", (occurrence_id,)
            ).fetchone()
            if row:
                if row[0] != "completed":
                    raise RuntimeError("occurrence outcome is ambiguous; reconcile before retry")
                return False
            self.conn.execute("INSERT INTO occurrences VALUES (?, 'started')", (occurrence_id,))
        await action()
        with self.conn:
            self.conn.execute(
                "UPDATE occurrences SET state='completed' WHERE id=?", (occurrence_id,)
            )
        return True

    def close(self) -> None:
        self.conn.close()


class Submit(BaseTool):
    name: str = "lab_submit"
    description: str = "Submit the fixed local report to the controlled lab service."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    risk: RiskLevel = RiskLevel.SENSITIVE
    endpoint: str
    workspace: Path

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        return CallAssessment(
            risk=self.risk,
            summary="submit local lab report",
            egress=True,
            egress_target="127.0.0.1",
            egress_configured=True,
        )

    async def execute(self, **_: Any) -> ToolResult:
        try:
            payload = json.loads((self.workspace / "report.json").read_text())
            return ToolResult(
                output=canonical(await asyncio.to_thread(request_local, self.endpoint, payload))
            )
        except (OSError, ValueError) as exc:
            return ToolResult.fail(f"local_submit:{type(exc).__name__}")


class Check(BaseTool):
    name: str = "lab_check"
    description: str = "Host-only independent artifact and service observation."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    endpoint: str
    workspace: Path

    async def execute(self, **_: Any) -> ToolResult:
        try:
            report_valid = valid_report(json.loads((self.workspace / "report.json").read_text()))
        except (OSError, ValueError):
            report_valid = False
        try:
            status = await asyncio.to_thread(request_local, self.endpoint)
            usable = True
        except (OSError, ValueError):
            status, usable = {}, False
        return ToolResult(
            output=canonical(
                {"report_valid": report_valid, "service_usable": usable, "status": status}
            )
        )


class Recorder(HeadlessUI):
    def __init__(self, path: Path, approve: bool = True):
        super().__init__(approve=approve)
        self.path = path

    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        super().on_tool_result(call, result)
        with self.path.open("a") as fh:
            fh.write(
                canonical(
                    {
                        "call_id": call.id,
                        "tool": call.name,
                        "args": call.arguments,
                        "ok": result.ok,
                        "output": result.output,
                        "error": result.error,
                    }
                )
                + "\n"
            )


def tc(name: str, **args: Any) -> ToolCall:
    return ToolCall(function=Function(name=name, arguments=canonical(args)))


class Trial:
    def __init__(self, path: Path, endpoint: str, *, approve: bool = True):
        path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.settings = Settings()
        self.settings.data_dir = path / "data"
        self.settings.agent.workspace = path / "workspace"
        self.settings.memory.enabled = False
        self.settings.ensure_dirs()
        self.settings.agent.workspace.mkdir(parents=True, exist_ok=True)
        self.goals = GoalStore(self.settings.goals_db)
        goals = self.goals.list()
        self.goal = (
            goals[0]
            if goals
            else self.goals.create(
                "Produce and submit an accepted report", steps=["Prepare report", "Get acceptance"]
            )
        )
        self.ui = Recorder(path / "calls.jsonl", approve)
        self.audit = AuditLog(path / "audit.jsonl", session_id=path.name)
        self.gate = Sentinel(self.settings.sentinel, self.audit, self.ui)
        self.submit = Submit(endpoint=endpoint, workspace=self.settings.agent.workspace)
        self.checker = Check(endpoint=endpoint, workspace=self.settings.agent.workspace)
        self.llm = MockLLM([])
        self.agent = MuseAgent(
            self.settings,
            self.llm,
            ToolCollection(
                Files(workspace=self.settings.agent.workspace),
                Goals(store=self.goals),
                self.submit,
                Terminate(),
            ),
            self.gate,
            self.ui,
            self.audit,
            goals=self.goals,
            session_file=path / "session.json",
            conversation_id=path.name,
        )
        self.contract = Contract(
            self.goal.id,
            1,
            tuple(
                Criterion(
                    metric,
                    f"lab://{path.name}/{metric}",
                    metric,
                    "eq",
                    True,
                    "boolean",
                    ("lab.independent.v1",),
                    max_age=60,
                    invariant=metric in ("window_open", "single_submission"),
                )
                for metric in ("report_valid", "accepted", "window_open", "single_submission")
            ),
        )
        self.ledger = Ledger(path / "evaluations.db")

    async def act(self, calls: list[ToolCall], *, crash: bool = False) -> str:
        def abrupt_exit(_: list[Message]) -> LLMResponse:
            os._exit(73)  # Intentional subprocess fault, without Agent finally/session persistence.

        self.llm.script.extend(
            [
                LLMResponse(tool_calls=calls),
                abrupt_exit
                if crash
                else LLMResponse(content="The scripted actor reports completion."),
            ]
        )
        return await self.agent.run("Advance the local report goal.")

    def write_report(self, filename: str = "report.json") -> ToolCall:
        return tc("files", action="write", path=filename, content=canonical(REPORT))

    def done(self) -> ToolCall:
        return tc("goals", action="set_status", goal_id=self.goal.id, status="done")

    async def snapshot(self, run: Run, now: int) -> tuple[list[Evidence], dict[str, Any]]:
        call = tc("lab_check")
        token = self.gate.begin_task("Independent local goal check", task_id=run.id)
        try:
            raw = await self.gate.guard(call, self.checker)
            self.ui.on_tool_result(call, raw)
        finally:
            self.gate.end_task(token)
        facts = json.loads(raw.output)
        evidence = []
        for c in self.contract.criteria:
            service = c.metric != "report_valid"
            value = facts["status"].get(c.metric, False) if service else facts["report_valid"]
            evidence.append(
                observation(
                    id=f"{run.id}:{run.attempt}:{c.id}",
                    goal_id=self.goal.id,
                    revision=1,
                    run_id=run.id,
                    attempt=run.attempt,
                    call_id=call.id,
                    criterion_id=c.id,
                    collector="lab.independent.v1",
                    observed_at=now,
                    collected_at=now,
                    subject=c.subject,
                    metric=c.metric,
                    value=value,
                    unit=c.unit,
                    ok=not service or (facts["service_usable"] and c.metric in facts["status"]),
                )
            )
        return evidence, facts

    async def measure(
        self,
        run: Run,
        now: int,
        previous: dict[str, Any] | None = None,
        *,
        stalled_runs: int = 0,
        reuse: tuple[list[Evidence], dict[str, Any]] | None = None,
        append: bool = True,
    ) -> dict[str, Any]:
        evidence, facts = reuse if reuse is not None else await self.snapshot(run, now)
        result = (
            self.ledger.record(self.contract, run, evidence, now=now, previous=previous)
            if append
            else evaluate(self.contract, run, evidence, now=now, previous=previous)
        )
        prefix = f"{run.id}.{run.attempt}" + (".view" if not append else "")
        write_json(
            self.path / f"{prefix}.snapshot.json",
            {
                "contract": asdict(self.contract),
                "run": asdict(run),
                "evidence": [asdict(e) for e in evidence],
                "now": now,
                "previous": previous,
            },
        )
        write_json(self.path / f"{prefix}.evaluation.json", result)
        goal = self.goals.get(self.goal.id)
        assert goal is not None
        return {
            "result": result,
            "decision": decide(result, stalled_runs=stalled_runs),
            "baseline_status": goal.status,
            "baseline_progress": goal.progress,
            "facts": facts,
        }

    def close(self) -> None:
        self.ledger.close()
        self.goals.close()


async def run_suite(output: Path) -> dict[str, Any]:
    # Fail rather than overwrite a previous run and destroy its evidence.
    output.mkdir(parents=True, exist_ok=False)
    cases = benchmark_cases()
    write_json(
        output / "manifest.json",
        {
            "suite_version": "local.v2",
            "python": sys.version.split()[0],
            "actor": "nanoMuse MockLLM scripted tool calls",
            "clock": "fixed logical seconds",
            "benchmark_sha256": hashlib.sha256(
                Path(__file__).with_name("benchmark.json").read_bytes()
            ).hexdigest(),
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True
            ).strip(),
            "expected_verdicts": {case["id"]: case["expected_verdict"] for case in cases},
        },
    )
    world = World(output / "oracle.db")
    rows = []
    try:
        for fixture in cases:
            case = fixture["id"]
            world.configure(
                case, available=case != "unsupported_done", window_open=case != "window_closed"
            )
            path = output / case
            trial = Trial(path, f"{world.url}/{case}", approve=case != "approval_denied")
            try:
                if case == "unsupported_done":
                    trial.contract = Contract(trial.goal.id, 1, (trial.contract.criteria[1],))
                before = await trial.measure(Run("baseline"), NOW)
                if case == "success":
                    await trial.act([trial.write_report(), tc("lab_submit"), trial.done()])
                elif case == "all_skipped":
                    await trial.act(
                        [
                            tc(
                                "goals",
                                action="update_step",
                                goal_id=trial.goal.id,
                                step_index=i,
                                status="skipped",
                            )
                            for i in (1, 2)
                        ]
                    )
                elif case == "unsupported_done":
                    await trial.act([trial.done()])
                elif case == "malformed_artifact":
                    await trial.act(
                        [
                            tc("files", action="write", path="report.json", content="{}"),
                            tc("lab_submit"),
                            trial.done(),
                        ]
                    )
                elif case == "stale_evidence":
                    await trial.act([trial.write_report(), tc("lab_submit"), trial.done()])
                    run = Run("work")
                    saved = await trial.snapshot(run, NOW + 1)
                    # A fresh snapshot view is saved, but not inserted as the immutable
                    # ledger input for this attempt. The ledger records its later review.
                    fresh = await trial.measure(
                        run, NOW + 1, before["result"], reuse=saved, append=False
                    )
                    # No refresh: the same run binding, but every observation is past its TTL.
                    final = await trial.measure(run, NOW + 62, fresh["result"], reuse=saved)
                elif case == "duplicate_trigger":
                    occurrence_path = path / "occurrences.db"

                    async def execute_once(owner: Trial = trial) -> None:
                        await owner.act([owner.write_report(), tc("lab_submit"), owner.done()])

                    deliveries = []
                    for _ in range(2):
                        host = OccurrenceHost(occurrence_path)
                        try:
                            deliveries.append(
                                await host.deliver("scheduled:report:1", execute_once)
                            )
                        finally:
                            host.close()
                    write_json(
                        path / "delivery.json",
                        {
                            "received": len(deliveries),
                            "executed": sum(deliveries),
                            "suppressed": len(deliveries) - sum(deliveries),
                            "host_only": True,
                            "persistent_reopen_between_deliveries": True,
                        },
                    )
                elif case == "busy_no_progress":
                    stalled = 0
                    timeline = []
                    for day in range(1, 4):
                        await trial.act(
                            [
                                trial.write_report("preparation.json"),
                                tc(
                                    "goals",
                                    action="update_step",
                                    goal_id=trial.goal.id,
                                    step_index=1,
                                    status="done",
                                    note=f"Preparation pass {day}",
                                ),
                            ]
                        )
                        current = await trial.measure(
                            Run(f"day_{day}"), NOW + day, before["result"]
                        )
                        stalled = stalled + 1 if current["result"]["delta"] == 0 else 0
                        current["decision"] = decide(current["result"], stalled_runs=stalled)
                        timeline.append(current)
                        before = current
                    write_json(path / "continuity.json", timeline)
                    final = timeline[-1]
                elif case in ("window_closed", "approval_denied"):
                    await trial.act([trial.write_report(), tc("lab_submit"), trial.done()])
                elif case == "revoked_acceptance":
                    await trial.act([trial.write_report(), tc("lab_submit"), trial.done()])
                    before = await trial.measure(Run("accepted"), NOW + 1, before["result"])
                    world.revoke(case)
                elif case.startswith("crash_"):
                    write_json(
                        path / "run_state.json",
                        {"run_id": "work", "attempt": 1, "state": "started"},
                    )
                    trial.close()
                    child = await asyncio.to_thread(
                        subprocess.run,
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--crash-worker",
                            str(path),
                            "--endpoint",
                            f"{world.url}/{case}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    if child.returncode != 73:
                        raise RuntimeError(
                            f"worker fault failed: {child.returncode}: {child.stderr[-1000:]}"
                        )
                    trial = Trial(path, f"{world.url}/{case}")
                    write_json(
                        path / "run_state.json",
                        {"run_id": "work", "attempt": 1, "state": "interrupted", "exitcode": 73},
                    )
                    assert world.truth(case)["submissions"] == 1
                    # A fresh GET of the actual service resolves the ambiguous commit.
                    reconciled = await trial.measure(
                        Run("work", 1, "interrupted"), NOW + 1, before["result"]
                    )
                    reconciled["session_saved_after_crash"] = (path / "session.json").exists()
                    write_json(path / "reconciliation.json", reconciled)
                    if case == "crash_naive_retry":
                        await trial.act([tc("lab_submit"), trial.done()])
                    else:
                        await trial.act([trial.done()])
                elif case not in ("success",):
                    raise ValueError(
                        f"no runtime fault injection defined for benchmark case {case}"
                    )
                if case not in ("busy_no_progress", "stale_evidence"):
                    final = await trial.measure(
                        Run("work", 2 if case.startswith("crash_") else 1),
                        NOW + 4,
                        before["result"],
                    )
                truth = world.truth(case)
                try:
                    artifact_valid = valid_report(
                        json.loads((trial.settings.agent.workspace / "report.json").read_text())
                    )
                except (OSError, ValueError):
                    artifact_valid = False
                calls = [
                    json.loads(line) for line in (path / "calls.jsonl").read_text().splitlines()
                ]
                actor_calls = [c for c in calls if c["tool"] != "lab_check"]
                row = {
                    "case": case,
                    "source_status": final["baseline_status"],
                    "source_progress": final["baseline_progress"],
                    "verdict": final["result"]["status"],
                    "delta": final["result"]["delta"],
                    "decision": final["decision"][0],
                    "criteria": final["result"]["criteria"],
                    "submissions": truth["submissions"],
                    "duplicate_submissions": max(0, truth["submissions"] - 1),
                    "actor_calls": len(actor_calls),
                    "failed_actor_calls": sum(not c["ok"] for c in actor_calls),
                    "unsupported_source_done": final["baseline_status"] == "done"
                    and final["result"]["status"] != "verified",
                    "actual_outcome": artifact_valid
                    and bool(truth["accepted"])
                    and bool(truth["window_open"])
                    and truth["submissions"] == 1,
                }
                row["expected_verdict"] = fixture["expected_verdict"]
                row["verdict_matches_fixture"] = row["verdict"] == fixture["expected_verdict"]
                for key in (
                    "expected_decision",
                    "expected_submissions",
                    "expected_source_status",
                    "outcome_truth",
                ):
                    row[key] = fixture[key]
                if case == "crash_naive_retry":
                    snapshot = json.loads((path / "work.2.snapshot.json").read_text())
                    weaker = replace(
                        trial.contract,
                        criteria=tuple(
                            c for c in trial.contract.criteria if c.id != "single_submission"
                        ),
                    )
                    row["without_uniqueness_constraint"] = evaluate(
                        weaker,
                        Run("work", 2),
                        [Evidence(**e) for e in snapshot["evidence"]],
                        now=snapshot["now"],
                    )["status"]
                rows.append(row)
                write_json(path / "summary.json", row)
            finally:
                trial.close()
        report = {
            "schema_version": 1,
            "mode": "scripted-runtime-local-oracle",
            "cases": rows,
            "case_count": len(rows),
            "unsupported_source_done": sum(r["unsupported_source_done"] for r in rows),
            "fixture_verdicts_matched": sum(r["verdict_matches_fixture"] for r in rows),
            "limits": [
                "scripted model, not a natural model error rate",
                "no production hooks",
                "duplicate delivery dedupe belongs to this experimental host, not product scheduler",
                "no production scheduler or Android lifecycle",
                "recommendations only; no completion enforcement",
            ],
        }
        write_json(output / "report.json", report)
        table = [
            "# Local goal evaluation run",
            "",
            "Scripted actor with actual nanoMuse tools, loopback service and independent checks.",
            "This is a fault-injection suite, not a model success-rate estimate.",
            "",
            "| Case | Source status | Evidence verdict | Decision | Submissions |",
            "|---|---|---|---|---|",
        ]
        table.extend(
            f"| {r['case']} | {r['source_status']} | {r['verdict']} | {r['decision']} | {r['submissions']} |"
            for r in rows
        )
        (output / "report.md").write_text("\n".join(table) + "\n")
        return report
    finally:
        world.close()


def replay(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    contract = Contract(
        data["contract"]["goal_id"],
        data["contract"]["revision"],
        tuple(
            Criterion(**{**c, "collectors": tuple(c["collectors"])})
            for c in data["contract"]["criteria"]
        ),
    )
    result = evaluate(
        contract,
        Run(**data["run"]),
        [Evidence(**e) for e in data["evidence"]],
        now=data["now"],
        previous=data["previous"],
    )
    expected = json.loads(
        path.with_name(path.name.replace(".snapshot.json", ".evaluation.json")).read_text()
    )
    if result != expected:
        raise AssertionError("replay differs from saved evaluation")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--crash-worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--endpoint", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.replay:
        print(canonical({"replayed": True, "evaluation_id": replay(args.replay)["id"]}))
    elif args.crash_worker:
        trial = Trial(args.crash_worker, args.endpoint)
        asyncio.run(trial.act([trial.write_report(), tc("lab_submit")], crash=True))
        raise AssertionError("fault injection did not terminate worker")
    elif args.output:
        report = asyncio.run(run_suite(args.output.resolve()))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["fixture_verdicts_matched"] != report["case_count"]:
            raise SystemExit(1)
    else:
        parser.error("provide --output or --replay")


if __name__ == "__main__":
    main()
