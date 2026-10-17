"""Independent scoring of persisted local runs against the versioned gold set.

Rechecks controller truth, tool-result references and all snapshots. Fails closed
on missing cases or edited gold. Generates individual case reports and a summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from prototype import EVALUATOR_VERSION, fingerprint

BENCHMARK = Path(__file__).with_name("benchmark.json")


def score(rows: list[dict[str, Any]], fixtures: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Denominators come from gold, never from the evaluator's own status labels."""
    ids = [r["case"] for r in rows]
    gold_ids = [r["id"] for r in fixtures]
    if (
        len(ids) != len(set(ids))
        or len(gold_ids) != len(set(gold_ids))
        or set(ids) != set(gold_ids)
    ):
        raise ValueError("results must match every unique fixture exactly once")
    by_id = {r["case"]: r for r in rows}
    pairs = [(by_id[f["id"]], f) for f in fixtures]
    negatives = [(r, f) for r, f in pairs if f["outcome_truth"] is False]
    verified = [(r, f) for r, f in pairs if r["verdict"] == "verified"]
    unsupported = [(r, f) for r, f in pairs if f["expected_verdict"] != "verified"]
    positives = [(r, f) for r, f in pairs if f["expected_verdict"] == "verified"]
    unknowns = [(r, f) for r, f in pairs if f["expected_verdict"] == "unknown"]
    source_done = [(r, f) for r, f in pairs if r["source_status"] == "done"]
    passed = sum(
        r["verdict"] == f["expected_verdict"]
        and r["decision"] == f["expected_decision"]
        and r["submissions"] == f["expected_submissions"]
        and r["source_status"] == f["expected_source_status"]
        and r["actual_outcome"] is f["outcome_truth"]
        for r, f in pairs
    )

    def ratio(n: int, d: int) -> dict[str, int]:
        return {"count": n, "total": d}

    return {
        "scenario_assertions": ratio(passed, len(pairs)),
        "source_false_completion": ratio(
            sum(r["source_status"] == "done" for r, _ in negatives), len(negatives)
        ),
        "eval_false_completion": ratio(
            sum(r["verdict"] == "verified" for r, _ in negatives), len(negatives)
        ),
        "verified_precision": ratio(
            sum(f["outcome_truth"] is True for _, f in verified), len(verified)
        ),
        "unsupported_verification": ratio(
            sum(r["verdict"] == "verified" for r, _ in unsupported), len(unsupported)
        ),
        "positive_controls": ratio(
            sum(r["verdict"] == "verified" for r, _ in positives), len(positives)
        ),
        "unknown_handling": ratio(
            sum(r["verdict"] == "unknown" for r, _ in unknowns), len(unknowns)
        ),
        "unverified_source_done": ratio(
            sum(r["verdict"] != "verified" for r, _ in source_done), len(source_done)
        ),
    }


def _actual_truth(case: Path, state: dict[str, Any]) -> bool:
    """Separate implementation from the service/checker schema helper."""
    try:
        artifact = json.loads((case / "workspace/report.json").read_text())
    except (OSError, ValueError):
        return False
    if type(artifact) is not dict or type(artifact.get("title")) is not str:
        return False
    if not artifact["title"].strip() or type(artifact.get("items")) is not list:
        return False
    if not artifact["items"] or any(
        type(item) is not str or not item.strip() for item in artifact["items"]
    ):
        return False
    return bool(state["accepted"] and state["window_open"] and state["submissions"] == 1)


def _traceable(
    snapshot: dict[str, Any], result: dict[str, Any], calls: dict[str, Any]
) -> tuple[int, int]:
    evidence = {e["id"]: e for e in snapshot["evidence"]}
    valid, total = 0, 0
    for criterion in snapshot["contract"]["criteria"]:
        cid = criterion["id"]
        if result["criteria"][cid] != "met":
            continue
        total += 1
        refs = result["evidence"].get(cid, [])
        good = bool(refs)
        for ref in refs:
            e = evidence.get(ref)
            if not e:
                good = False
                continue
            call = calls.get(e["call_id"], {})
            raw = json.loads(call.get("output") or "{}")
            fact = (
                raw.get("report_valid")
                if e["metric"] == "report_valid"
                else raw.get("status", {}).get(e["metric"])
            )
            good = good and (
                e["payload_digest"]
                == fingerprint({k: v for k, v in e.items() if k not in ("id", "payload_digest")})
                and e["goal_id"] == snapshot["contract"]["goal_id"]
                and e["revision"] == snapshot["contract"]["revision"]
                and e["run_id"] == snapshot["run"]["id"]
                and e["attempt"] == snapshot["run"]["attempt"]
                and e["subject"] == criterion["subject"]
                and e["collector"] in criterion["collectors"]
                and e["ok"] is True
                and e["allowed"] is True
                and 0 <= snapshot["now"] - e["observed_at"] <= criterion["max_age"]
                and call.get("tool") == "lab_check"
                and call.get("ok") is True
                and type(fact) is type(e["value"])
                and fact == e["value"]
            )
        valid += bool(good)
    return valid, total


def audit_run(directory: Path, benchmark: Path = BENCHMARK) -> dict[str, Any]:
    from local_harness import replay

    dataset = json.loads(benchmark.read_text())
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["benchmark_sha256"] != hashlib.sha256(benchmark.read_bytes()).hexdigest():
        raise ValueError("gold dataset differs from the executed benchmark")
    original = json.loads((directory / "report.json").read_text())
    rows = original["cases"]
    metrics = score(rows, dataset["cases"])
    replayed, referenced, traceable = 0, 0, 0
    details = []
    with sqlite3.connect(directory / "oracle.db") as conn:
        conn.row_factory = sqlite3.Row
        for row in rows:
            case = directory / row["case"]
            fixture = next(f for f in dataset["cases"] if f["id"] == row["case"])
            state = dict(conn.execute("SELECT * FROM states WHERE id=?", (row["case"],)).fetchone())
            actual = _actual_truth(case, state)
            if actual is not row["actual_outcome"] or state["submissions"] != row["submissions"]:
                raise ValueError(f"report differs from controller truth: {row['case']}")
            calls = {
                c["call_id"]: c
                for c in (
                    json.loads(line) for line in (case / "calls.jsonl").read_text().splitlines()
                )
            }
            snapshots = list(case.glob("*.snapshot.json"))
            if not snapshots:
                raise ValueError("case has no evidence snapshots")
            latest: dict[str, Any] = {}
            for path in snapshots:
                result = replay(path)
                snap = json.loads(path.read_text())
                good, count = _traceable(snap, result, calls)
                traceable += good
                referenced += count
                replayed += 1
                if result.get("at", -1) >= latest.get("at", -1):
                    latest = result
            if latest["status"] != row["verdict"] or latest["criteria"] != row["criteria"]:
                raise ValueError("case summary differs from its final evaluation")
            with sqlite3.connect(case / "data/goals.db") as goals:
                stored = goals.execute(
                    "SELECT status FROM goals WHERE id=?", (latest["goal_id"],)
                ).fetchone()
            if not stored or stored[0] != row["source_status"]:
                raise ValueError("reported source status differs from GoalStore")
            detail = {
                **row,
                "name": fixture["name"],
                "purpose": fixture["purpose"],
                "gold_rationale": fixture["gold_rationale"],
                "fault": fixture["fault"],
                "outcome_met": latest["outcome_met"],
                "outcome_required": latest["outcome_required"],
                "constraint_met": latest["constraint_met"],
                "constraint_required": latest["constraint_required"],
                "snapshot_count": len(snapshots),
            }
            details.append(detail)
            text = [
                f"# {fixture['name']}",
                "",
                fixture["purpose"],
                "",
                f"故障设置：{fixture['fault']}",
                "",
                f"标准答案：{fixture['expected_verdict']}，{fixture['expected_decision']}，提交 {fixture['expected_submissions']} 次。",
                f"实测：原有状态 {row['source_status']}；独立评价 {row['verdict']}；建议 {row['decision']}；提交 {row['submissions']} 次。",
                "",
                f"成果条件 {len(latest['outcome_met'])}/{latest['outcome_required']}；约束条件 {len(latest['constraint_met'])}/{latest['constraint_required']}；delta={row['delta']}。",
                "",
                f"真值依据：{fixture['gold_rationale']}",
                "",
                "证据位置：本目录的 `*.snapshot.json`、`*.evaluation.json`、`calls.jsonl`、`audit.jsonl`；隐藏服务状态位于上级 `oracle.db`。",
                "",
                f"重放检查：{len(snapshots)} 份完整快照结果一致。",
            ]
            (case / "case-report.md").write_text("\n".join(text) + "\n")
    metrics["evidence_traceability"] = {"count": traceable, "total": referenced}
    metrics["replay_consistency"] = {"count": replayed, "total": replayed}
    gates = (
        metrics["scenario_assertions"]["count"] == len(rows)
        and metrics["eval_false_completion"]["count"] == 0
        and metrics["unsupported_verification"]["count"] == 0
        and metrics["positive_controls"]["count"] == metrics["positive_controls"]["total"]
        and traceable == referenced
        and referenced > 0
        and replayed > 0
    )
    report = {
        "benchmark_version": dataset["version"],
        "evaluator_version": EVALUATOR_VERSION,
        "metrics": metrics,
        "gates_passed": gates,
        "cases": details,
        "manifest": manifest,
        "limits": original["limits"],
    }
    (directory / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# GoalEval v1 本机评测报告",
        "",
        "固定故障注入集，结果不代表真实模型或长期运行成功率。",
        "",
        "| 场景 | 原有状态 | 标准答案 | 实测评价 | 下一步建议 | 提交次数 | 成果条件 | 约束条件 |",
        "|---|---|---|---|---|---:|---|---|",
    ]
    lines.extend(
        f"| [{r['name']}]({r['case']}/case-report.md) | {r['source_status']} | {r['expected_verdict']} | {r['verdict']} | {r['decision']} | {r['submissions']} | {len(r['outcome_met'])}/{r['outcome_required']} | {len(r['constraint_met'])}/{r['constraint_required']} |"
        for r in details
    )
    lines += ["", "| 指标 | 数量 / 分母 |", "|---|---|"]
    lines.extend(f"| {key} | {v['count']}/{v['total']} |" for key, v in metrics.items())
    lines += [
        "",
        f"验收门槛：{'通过' if gates else '未通过'}。",
        "",
        "原有错误完成与未核验完成单独计数。约束满足不计入成果进度。评价建议未介入产品状态。",
    ]
    (directory / "evaluation-report.md").write_text("\n".join(lines) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    report = audit_run(args.run_directory)
    print(
        json.dumps({"gates_passed": report["gates_passed"], "metrics": report["metrics"]}, indent=2)
    )
    if not report["gates_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
