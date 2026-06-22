"""Scorers for the CritCom eval harness.

Five scorers, each grounded in a published 2025-2026 evaluation framework:

1. classification — ACR Cat1/Cat2/Cat3 confusion matrix.
   (ART benchmark / MedAgentBench)
2. trajectory  — tool selection + order correctness.
   (TRAJECT-Bench, 2025)
3. state       — Communication + Task resources present in FHIR audit.
   (FHIR-AgentEval / FHIR-AgentBench, 2025-2026)
4. deadlines   — dispatched deadline meets ACR maximum (60 min / 24 hr).
   (ACR Practice Parameter for the Communication of Diagnostic Imaging Findings)
5. reliability — pass^k across k independent runs.
   (tau-bench, Sierra Research)
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

ACR_CATEGORIES = ("Cat1", "Cat2", "Cat3", "None")
_LITERAL_TOOL_PATTERN = re.compile(r"\b([a-z_]+_tool)\b", re.IGNORECASE)

# Natural-language fingerprints for each tool. The agent's narrative rarely
# names tools literally — it says "dispatched a communication" instead of
# "dispatch_communication_tool". Each entry below is (tool_name, regex) and
# the FIRST regex to match in the text means that tool fired.
_TOOL_FINGERPRINTS: list[tuple[str, re.Pattern[str]]] = [
    ("fetch_report_fhir_tool", re.compile(
        r"\b(?:fetch(?:ed)?|retriev(?:ed?|ing)|pull(?:ed)?)\s+(?:the\s+)?"
        r"(?:diagnostic\s+)?report|\bDiagnosticReport\s+(?:dr-|\w)|"
        r"\bfetch_report_fhir", re.IGNORECASE)),
    ("fetch_report_dicom_tool", re.compile(
        r"\b(?:DICOM\s+(?:worklist|study|accession)|modality\s+worklist|"
        r"C-FIND|fetch_report_dicom)", re.IGNORECASE)),
    ("fetch_radiologist_findings_tool", re.compile(
        r"\b(?:radiologist['’]?s?\s+findings|signed\s+findings|report\s+broker|"
        r"fetch_radiologist_findings)", re.IGNORECASE)),
    ("resolve_provider_tool", re.compile(
        r"\b(?:resolve(?:d)?\s+(?:the\s+)?(?:ordering\s+)?(?:provider|physician)|"
        r"ordering\s+physician|practitioner-\d+|"
        r"\bDr\.\s+\w+|\bresolve_provider)", re.IGNORECASE)),
    ("dispatch_communication_tool", re.compile(
        r"\b(?:dispatch(?:ed|ing)?\s+(?:a\s+|the\s+)?(?:notification|communication|"
        r"message|alert|page)|Communication\s+(?:\d+|created|sent|resource)|"
        r"notif(?:y|ied)\s+(?:the\s+)?(?:physician|provider|doctor)|"
        r"dispatch_communication)", re.IGNORECASE)),
    ("track_acknowledgment_tool", re.compile(
        r"\b(?:acknowledg(?:ment|ement)\s+(?:task|window|timer)|"
        r"Task\s+(?:\d+|created|opened|task-\w+)|"
        r"(?:60|1440|24)[\s-]?(?:minute|hour)\s+(?:deadline|window|ack)|"
        r"track_acknowledgment)", re.IGNORECASE)),
    ("escalate_tool", re.compile(
        r"\b(?:escalat(?:ed?|ion|ing)|on[\s-]call\s+(?:provider|attending|doctor)|"
        r"reassign(?:ed)?\s+to|escalate_tool)", re.IGNORECASE)),
    ("query_audit_tool", re.compile(
        r"\b(?:audit\s+(?:trail|history|log)|query_audit|"
        r"Communication\s+and\s+Task\s+history)", re.IGNORECASE)),
]


# -----------------------------------------------------------------------------
# Per-run extraction helpers
# -----------------------------------------------------------------------------

def extract_category(reply_text: str) -> str | None:
    """Pull the ACR category mentioned in the agent's final reply, if any.

    Falls back to inferring from deadline language (60-min → Cat1, 24-hour
    or 1440-min → Cat2) and from "no critical communication" → Cat3.
    """
    if not reply_text:
        return None
    for cat in ("Cat1", "Cat2", "Cat3"):
        if re.search(rf"\b{cat}\b", reply_text):
            return cat
    # Infer Cat1 from a 60-minute deadline.
    if re.search(r"\b60[\s-]?(?:minute|min)\b", reply_text, re.IGNORECASE):
        return "Cat1"
    # Infer Cat2 from a 24-hour / 1440-minute deadline.
    if re.search(r"\b(?:24[\s-]?hour|1440[\s-]?(?:minute|min))\b", reply_text, re.IGNORECASE):
        return "Cat2"
    # Cat3 implicit signal.
    if re.search(r"\b(no critical|not critical|routine|non-critical|"
                 r"no\s+(?:critical\s+)?communication\s+(?:is\s+)?needed)\b",
                 reply_text, re.IGNORECASE):
        return "Cat3"
    return None


def extract_tools(reply_text: str) -> list[str]:
    """Return the tools the agent appears to have called, in order of evidence.

    Combines literal mentions (`dispatch_communication_tool`) with
    natural-language fingerprints (`dispatched a communication`). The agent
    rarely names tools literally, so the fingerprints carry most of the load.

    Note: this reads the agent's narrative, not its actual tool calls. State
    validation (scorer 3) confirms the side effects actually happened on HAPI.
    """
    if not reply_text:
        return []
    seen: list[str] = []

    # 1. Literal tool names — pick these up first so they preserve order.
    for match in _LITERAL_TOOL_PATTERN.finditer(reply_text):
        name = match.group(1).lower()
        if name in seen:
            continue
        seen.append(name)

    # 2. Natural-language fingerprints — add any tool that has evidence.
    for tool_name, pattern in _TOOL_FINGERPRINTS:
        if tool_name in seen:
            continue
        if pattern.search(reply_text):
            seen.append(tool_name)

    return seen


def extract_deadline_minutes(reply_text: str) -> int | None:
    """Pull the acknowledgment-window minutes the agent set, if any."""
    if not reply_text:
        return None
    m = re.search(r"(\d{2,5})\s*(?:-|\s)?\s*minute", reply_text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{1,3})\s*(?:-|\s)?\s*hour", reply_text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 60
    return None


# -----------------------------------------------------------------------------
# 1. Classification — confusion matrix
# -----------------------------------------------------------------------------

@dataclass
class ClassificationReport:
    confusion: dict[str, dict[str, int]]
    per_class: dict[str, dict[str, float]]
    accuracy: float
    n: int


def score_classification(pairs: Iterable[tuple[str, str | None]]) -> ClassificationReport:
    """pairs is (expected_category, predicted_category). Missing prediction -> 'None'."""
    confusion: dict[str, dict[str, int]] = {c: {c2: 0 for c2 in ACR_CATEGORIES} for c in ACR_CATEGORIES}
    correct = 0
    n = 0
    for expected, predicted in pairs:
        if expected not in ACR_CATEGORIES:
            continue
        pred = predicted if predicted in ACR_CATEGORIES else "None"
        confusion[expected][pred] += 1
        if pred == expected:
            correct += 1
        n += 1

    per_class: dict[str, dict[str, float]] = {}
    for cat in ACR_CATEGORIES:
        tp = confusion[cat][cat]
        fn = sum(confusion[cat][c] for c in ACR_CATEGORIES if c != cat)
        fp = sum(confusion[other][cat] for other in ACR_CATEGORIES if other != cat)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[cat] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}

    accuracy = correct / n if n else 0.0
    return ClassificationReport(confusion=confusion, per_class=per_class, accuracy=accuracy, n=n)


# -----------------------------------------------------------------------------
# 2. Trajectory — tool selection + order
# -----------------------------------------------------------------------------

@dataclass
class TrajectoryScore:
    selection_f1: float       # set-overlap between expected and actual tools
    order_match: bool         # actual tools appear in expected order (subseq match)
    missing: list[str]
    extra: list[str]


def score_trajectory(expected: list[str], actual: list[str]) -> TrajectoryScore:
    exp_set, act_set = set(expected), set(actual)
    tp = len(exp_set & act_set)
    precision = tp / len(act_set) if act_set else 0.0
    recall = tp / len(exp_set) if exp_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    missing = [t for t in expected if t not in act_set]
    extra = [t for t in actual if t not in exp_set]

    # Order: walk through actual, see if expected tools appear as a subsequence
    order_match = _is_subsequence([t for t in actual if t in exp_set], expected)

    return TrajectoryScore(
        selection_f1=f1, order_match=order_match, missing=missing, extra=extra
    )


def _is_subsequence(seq: list[str], target: list[str]) -> bool:
    it = iter(target)
    return all(any(s == t for t in it) for s in seq)


# -----------------------------------------------------------------------------
# 3. State validation — FHIR Communication + Task were actually written
# -----------------------------------------------------------------------------

@dataclass
class StateScore:
    communication_present: bool
    task_present: bool
    task_deadline_minutes: int | None
    raw_audit: dict | None = None


_REAL_COMM_PATTERN = re.compile(
    r"\bCommunication\s+(?:\d+|created|dispatched|sent|opened|resource|/\d+)", re.IGNORECASE
)
_REAL_TASK_PATTERN = re.compile(
    r"\bTask\s+(?:\d+|created|opened|tracked|resource|/\d+)", re.IGNORECASE
)


def score_state_from_fhir(
    communication_present: bool,
    task_present: bool,
    deadline_minutes: int | None,
    expected_category: str,
) -> StateScore:
    """Build a StateScore from resources read directly off HAPI.

    Authoritative path used by the runner when a FHIR base URL is configured —
    it confirms the side effects actually landed rather than trusting the
    agent's narrative. For Cat3 we want ABSENCE, so the booleans are inverted.
    """
    if expected_category == "Cat3":
        return StateScore(
            communication_present=not communication_present,
            task_present=not task_present,
            task_deadline_minutes=None,
        )
    return StateScore(
        communication_present=communication_present,
        task_present=task_present,
        task_deadline_minutes=deadline_minutes,
    )


def score_state(audit_reply_text: str, expected_category: str) -> StateScore:
    """Best-effort parse of a query_audit_tool reply to confirm Communication + Task creation.

    Fallback path, used only when the FHIR server is not reachable from the eval
    harness (see score_state_from_fhir for the authoritative check). It reads the
    agent's narrative, which can assert success without it being true.

    For Cat3 cases we want ABSENCE of these resources, so we invert: a reply that
    does not name a real Communication / Task counts as a pass.
    """
    text = audit_reply_text or ""
    has_real_comm = bool(_REAL_COMM_PATTERN.search(text))
    has_real_task = bool(_REAL_TASK_PATTERN.search(text))
    deadline = extract_deadline_minutes(text)
    if expected_category == "Cat3":
        return StateScore(
            communication_present=not has_real_comm,
            task_present=not has_real_task,
            task_deadline_minutes=None,
        )
    return StateScore(
        communication_present=has_real_comm,
        task_present=has_real_task,
        task_deadline_minutes=deadline,
    )


# -----------------------------------------------------------------------------
# 4. Deadline compliance vs ACR maximum
# -----------------------------------------------------------------------------

ACR_MAX_MINUTES = {"Cat1": 60, "Cat2": 1440, "Cat3": None, "None": None}


def score_deadline(expected_category: str, dispatched_minutes: int | None) -> bool:
    cap = ACR_MAX_MINUTES.get(expected_category)
    if cap is None:
        # Cat3 / None — no Communication should fire, so dispatched_minutes should be None.
        return dispatched_minutes is None
    if dispatched_minutes is None:
        return False
    return dispatched_minutes <= cap


# -----------------------------------------------------------------------------
# 5. Reliability — pass^k across k runs
# -----------------------------------------------------------------------------

@dataclass
class ReliabilityScore:
    k: int
    per_case_pass_rate: dict[str, float]   # case_id -> mean(pass) across runs
    pass_at_k: float                       # fraction of cases where ALL k runs passed


def score_reliability(case_runs: dict[str, list[bool]]) -> ReliabilityScore:
    """case_runs is case_id -> list of boolean pass/fail across k runs."""
    if not case_runs:
        return ReliabilityScore(k=0, per_case_pass_rate={}, pass_at_k=0.0)
    k = max(len(v) for v in case_runs.values())
    per_case_rate = {cid: sum(v) / len(v) if v else 0.0 for cid, v in case_runs.items()}
    pass_k = sum(1 for v in case_runs.values() if v and all(v)) / len(case_runs)
    return ReliabilityScore(k=k, per_case_pass_rate=per_case_rate, pass_at_k=pass_k)


# -----------------------------------------------------------------------------
# Aggregator
# -----------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    expected_category: str
    predicted_category: str | None
    classification_correct: bool
    trajectory: TrajectoryScore
    state: StateScore
    deadline_compliant: bool
    overall_pass: bool
    elapsed_seconds: float
    error: str | None = None


@dataclass
class EvalSummary:
    n_cases: int
    classification: ClassificationReport
    trajectory_f1_mean: float
    trajectory_order_rate: float
    state_pass_rate: float
    deadline_pass_rate: float
    overall_pass_rate: float
    elapsed_seconds_mean: float
    reliability: ReliabilityScore | None = None
    per_case: list[CaseResult] = field(default_factory=list)


def aggregate(results: list[CaseResult], reliability: ReliabilityScore | None = None) -> EvalSummary:
    n = len(results)
    if n == 0:
        return EvalSummary(
            n_cases=0,
            classification=ClassificationReport({}, {}, 0.0, 0),
            trajectory_f1_mean=0.0,
            trajectory_order_rate=0.0,
            state_pass_rate=0.0,
            deadline_pass_rate=0.0,
            overall_pass_rate=0.0,
            elapsed_seconds_mean=0.0,
            reliability=reliability,
            per_case=[],
        )
    cls = score_classification((r.expected_category, r.predicted_category) for r in results)
    return EvalSummary(
        n_cases=n,
        classification=cls,
        trajectory_f1_mean=sum(r.trajectory.selection_f1 for r in results) / n,
        trajectory_order_rate=sum(1 for r in results if r.trajectory.order_match) / n,
        state_pass_rate=sum(1 for r in results if r.state.communication_present and r.state.task_present) / n,
        deadline_pass_rate=sum(1 for r in results if r.deadline_compliant) / n,
        overall_pass_rate=sum(1 for r in results if r.overall_pass) / n,
        elapsed_seconds_mean=sum(r.elapsed_seconds for r in results) / n,
        reliability=reliability,
        per_case=results,
    )
