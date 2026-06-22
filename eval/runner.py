"""Eval runner. Hits the agent, captures replies, scores results, writes
JSON + markdown reports under eval/reports/."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone

from eval import client, fhir_state, report, scorers

FIXTURES_PATH = pathlib.Path(__file__).parent / "fixtures" / "labeled_cases.json"
REPORTS_DIR = pathlib.Path(__file__).parent / "reports"
DEFAULT_BASE_URL = "http://localhost:8002"


def _load_cases() -> list[dict]:
    payload = json.loads(FIXTURES_PATH.read_text())
    return payload["cases"]


def _audit_prompt(case: dict) -> str:
    """Follow-up prompt to query the FHIR audit state for a finished case."""
    sr = case.get("service_request_id")
    if sr:
        return f"Use query_audit_tool to return the full Communication and Task history for service_request_id {sr}."
    pid = case.get("patient_id")
    if pid:
        return f"Use query_audit_tool to return the full Communication and Task history for patient_id {pid}."
    rid = case.get("report_id")
    return f"Use query_audit_tool to return the full Communication and Task history for accession {rid}."


def run_case(
    case: dict,
    base_url: str,
    audit: bool,
    delay: float,
    fhir_base_url: str | None = None,
) -> scorers.CaseResult:
    expected_cat = case["expected_category"]
    expected_tools = case["expected_tools"]

    main = client.send(base_url, case["prompt"])
    if not main.success:
        return scorers.CaseResult(
            case_id=case["case_id"],
            expected_category=expected_cat,
            predicted_category=None,
            classification_correct=False,
            trajectory=scorers.TrajectoryScore(0.0, False, expected_tools, []),
            state=scorers.StateScore(False, False, None),
            deadline_compliant=False,
            overall_pass=False,
            elapsed_seconds=main.elapsed_seconds,
            error=main.error,
        )

    predicted_cat = scorers.extract_category(main.text)
    actual_tools = scorers.extract_tools(main.text)
    dispatched_min = scorers.extract_deadline_minutes(main.text)

    cls_correct = predicted_cat == expected_cat
    traj = scorers.score_trajectory(expected_tools, actual_tools)

    # State validity: prefer a direct read off HAPI (authoritative), fall back
    # to parsing the agent's audit narrative when FHIR isn't reachable.
    sr_id = case.get("service_request_id")
    state = None
    if fhir_base_url and sr_id:
        if delay:
            time.sleep(delay)
        fs = fhir_state.check_state(fhir_base_url, sr_id)
        if fs.reachable:
            state = scorers.score_state_from_fhir(
                fs.communication_present, fs.task_present, fs.task_deadline_minutes, expected_cat
            )
            # A Task's persisted ack window is more reliable than parsed prose.
            if fs.task_deadline_minutes is not None:
                dispatched_min = fs.task_deadline_minutes

    if state is None:
        state_text = ""
        if audit and expected_cat != "Cat3":
            if delay:
                time.sleep(delay)
            audit_reply = client.send(base_url, _audit_prompt(case))
            state_text = audit_reply.text if audit_reply.success else ""
        state = scorers.score_state(state_text or main.text, expected_cat)

    ddl = scorers.score_deadline(expected_cat, dispatched_min)

    overall = (
        cls_correct
        and traj.selection_f1 >= 0.75
        and ddl
        and (expected_cat == "Cat3" or (state.communication_present and state.task_present))
    )

    return scorers.CaseResult(
        case_id=case["case_id"],
        expected_category=expected_cat,
        predicted_category=predicted_cat,
        classification_correct=cls_correct,
        trajectory=traj,
        state=state,
        deadline_compliant=ddl,
        overall_pass=overall,
        elapsed_seconds=main.elapsed_seconds,
        error=None,
    )


def run_all(
    base_url: str = DEFAULT_BASE_URL,
    k: int = 1,
    audit: bool = True,
    delay: float = 2.0,
    limit: int | None = None,
    case_ids: list[str] | None = None,
    fhir_base_url: str | None = None,
) -> scorers.EvalSummary:
    """Run the full eval suite. Returns the aggregated EvalSummary."""
    cases = _load_cases()
    if case_ids:
        cases = [c for c in cases if c["case_id"] in case_ids]
    if limit:
        cases = cases[:limit]

    case_runs: dict[str, list[bool]] = defaultdict(list)
    last_results: dict[str, scorers.CaseResult] = {}

    for run_idx in range(k):
        for case in cases:
            result = run_case(case, base_url, audit=audit, delay=delay, fhir_base_url=fhir_base_url)
            case_runs[case["case_id"]].append(result.overall_pass)
            last_results[case["case_id"]] = result
            print(f"  [run {run_idx + 1}/{k}] {case['case_id']}: "
                  f"{'PASS' if result.overall_pass else 'FAIL'} "
                  f"(cat {result.predicted_category}/{result.expected_category}, "
                  f"traj F1 {result.trajectory.selection_f1:.2f}, "
                  f"{result.elapsed_seconds:.1f}s"
                  f"{', err=' + result.error if result.error else ''})")
            if delay:
                time.sleep(delay)

    reliability = scorers.score_reliability(dict(case_runs)) if k > 1 else None
    summary = scorers.aggregate(list(last_results.values()), reliability=reliability)
    return summary


def _save_reports(summary: scorers.EvalSummary, base_url: str) -> tuple[pathlib.Path, pathlib.Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = REPORTS_DIR / f"eval-{ts}.json"
    md_path = REPORTS_DIR / f"eval-{ts}.md"

    json_path.write_text(json.dumps(_summary_to_dict(summary, base_url), indent=2, default=str))
    md_path.write_text(report.render_markdown(summary, base_url=base_url, generated_at=ts))
    return json_path, md_path


def _summary_to_dict(summary: scorers.EvalSummary, base_url: str) -> dict:
    return {
        "base_url": base_url,
        "n_cases": summary.n_cases,
        "classification": asdict(summary.classification),
        "trajectory_f1_mean": summary.trajectory_f1_mean,
        "trajectory_order_rate": summary.trajectory_order_rate,
        "state_pass_rate": summary.state_pass_rate,
        "deadline_pass_rate": summary.deadline_pass_rate,
        "overall_pass_rate": summary.overall_pass_rate,
        "elapsed_seconds_mean": summary.elapsed_seconds_mean,
        "reliability": asdict(summary.reliability) if summary.reliability else None,
        "per_case": [asdict(r) for r in summary.per_case],
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Run the CritCom eval harness.")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Agent base URL.")
    p.add_argument("--k", type=int, default=1, help="Repeated runs per case for pass^k.")
    p.add_argument("--no-audit", action="store_true", help="Skip the query_audit follow-up call.")
    p.add_argument("--delay", type=float, default=2.0, help="Seconds to sleep between requests.")
    p.add_argument("--limit", type=int, default=None, help="Run only the first N cases.")
    p.add_argument("--case", action="append", dest="case_ids", default=None, help="Run only this case_id (repeatable).")
    p.add_argument(
        "--fhir-base-url",
        default=os.getenv("CRITCOM_EVAL_FHIR_BASE_URL"),
        help="HAPI FHIR base URL for direct state validation. Defaults to "
        "$CRITCOM_EVAL_FHIR_BASE_URL. If unset, state is parsed from the agent narrative.",
    )
    args = p.parse_args()

    fhir_note = args.fhir_base_url or "narrative-parse (no FHIR URL)"
    print(f"CritCom eval — target {args.base_url}, k={args.k}, audit={not args.no_audit}, state={fhir_note}")
    summary = run_all(
        base_url=args.base_url,
        k=args.k,
        audit=not args.no_audit,
        delay=args.delay,
        limit=args.limit,
        case_ids=args.case_ids,
        fhir_base_url=args.fhir_base_url,
    )
    json_path, md_path = _save_reports(summary, args.base_url)
    print()
    print(f"Overall pass rate: {summary.overall_pass_rate:.1%} ({summary.n_cases} cases)")
    print(f"Classification accuracy: {summary.classification.accuracy:.1%}")
    print(f"Trajectory F1 mean: {summary.trajectory_f1_mean:.2f}")
    print(f"Deadline compliance: {summary.deadline_pass_rate:.1%}")
    print(f"State pass: {summary.state_pass_rate:.1%}")
    if summary.reliability:
        print(f"pass^{summary.reliability.k}: {summary.reliability.pass_at_k:.1%}")
    print()
    print(f"Wrote {json_path.name} and {md_path.name} to {REPORTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
