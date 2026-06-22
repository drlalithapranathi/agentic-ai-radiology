# CritCom evaluation harness

This directory contains the **performance evaluation framework** for the
CritCom critical-results agent. It implements the five metrics that the
2025–2026 clinical-agent benchmark literature converges on.

## Why not a single off-the-shelf benchmark?

Generic agent benchmarks (OSWorld, AgentBench) score GUI or toy-domain
tool use — they don't measure clinical correctness or ACR-deadline
compliance. The literature for FHIR-native clinical agents (FHIR-AgentEval
PMC 2026, FHIR-AgentBench arXiv 2509.19319) instead defines evaluation as
**(a) state-based validation on a resettable FHIR server**, **(b)
trajectory-aware tool-use scoring**, **(c) reliability across repeated
runs**. CritCom's harness combines those with two domain-specific scorers
(ACR classification, ACR deadline compliance).

## The five metrics

| # | Metric | Implemented in | Source |
|---|---|---|---|
| 1 | ACR classification confusion matrix (Cat1 / Cat2 / Cat3 / None) | `scorers.score_classification` | ART benchmark, MedAgentBench |
| 2 | Trajectory F1 + order correctness over the tool chain | `scorers.score_trajectory` | TRAJECT-Bench (arXiv 2510.04550, Oct 2025) |
| 3 | State validity — Communication + Task resources present in HAPI | `scorers.score_state` | FHIR-AgentEval (PMC 2026), FHIR-AgentBench (arXiv 2509.19319) |
| 4 | Deadline compliance vs ACR Practice Parameter (Cat1≤60min, Cat2≤24h) | `scorers.score_deadline` | ACR Practice Parameter for the Communication of Diagnostic Imaging Findings |
| 5 | `pass^k` reliability across k independent runs | `scorers.score_reliability` | τ-bench (Sierra Research, arXiv 2406.12045); ReliabilityBench (arXiv 2601.06112) |

## How to run

**Preferred path — Docker (matches the prof's "dockerize everything" ask):**

```bash
# Full eval against the in-network agent (HAPI + Orthanc + seed all auto-up)
docker compose --profile eval run --rm critcom-eval

# Faster — skip the query_audit follow-up
docker compose --profile eval run --rm critcom-eval --no-audit

# Reliability — every case runs 3 times, reports pass^3
docker compose --profile eval run --rm critcom-eval --k 3

# Run only one labeled case
docker compose --profile eval run --rm critcom-eval --case fhir-cat1-aortic-dissection

# Target a remote agent instead of the local stack
docker compose --profile eval run --rm \
  -e CRITCOM_EVAL_BASE_URL=http://<your-agent-host>:<port> \
  critcom-eval
```

See `DOCKER.md` at the repo root for the full layout (which service talks
to which, what port, etc.).

**Direct python (for fast iteration on the scorers themselves):**

```bash
python -m eval                                       # default: http://localhost:8002
python -m eval --base-url http://<your-host>:<port>  # remote agent
python -m eval --k 3                                 # pass^3 reliability
python -m eval --case fhir-cat1-aortic-dissection    # one case only
python -m eval --no-audit                            # skip audit follow-up
python -m eval --fhir-base-url http://localhost:8081/fhir  # verify state on HAPI directly
```

Reports are written to `eval/reports/eval-<UTC>.json` and `eval/reports/eval-<UTC>.md`.

**State validation source.** When a FHIR base URL is available — via
`--fhir-base-url` or `$CRITCOM_EVAL_FHIR_BASE_URL` (set automatically for the
docker `critcom-eval` service) — scorer 3 reads `Communication` and `Task`
straight off HAPI for the case's `service_request_id`, which is authoritative.
If no FHIR URL is reachable, it falls back to parsing the agent's audit
narrative. The docker path sets this for you; direct-python runs against the
local stack should pass `--fhir-base-url http://localhost:8081/fhir`.

## Scaling the fixture set

`fixtures/labeled_cases.json` ships with **7 labeled cases** matching the
existing seed bundles (4 FHIR DiagnosticReports + 3 DICOM findings).
For higher statistical power:

1. Add new entries to `labeled_cases.json` with a `case_id`, `prompt`,
   `expected_category`, `expected_tools`, and (optionally)
   `expected_deadline_minutes`.
2. Seed the corresponding `DiagnosticReport` to HAPI (for FHIR cases) or
   add a `<accession>.json` under `tests/fixtures/dicom_findings/` (for
   DICOM cases) and redeploy.
3. Re-run `python -m eval` — the new cases will be picked up automatically.

A balanced set of **30 cases** (10 Cat1, 10 Cat2, 10 Cat3) is the
literature norm for reportable confusion matrices.

## Notes & caveats

- **Trajectory scoring reads the agent's narrative reply, not its internal
  tool-call list.** This is a *conservative lower bound* — silent tools
  won't be credited. To upgrade to ground-truth trace capture, add ADK
  callback hooks to log tool invocations.
- **State scoring queries HAPI directly when a FHIR URL is configured**
  (see "State validation source" above); it only falls back to narrative
  parsing when no FHIR server is reachable. Narrative parsing can credit a
  case the agent merely *claims* to have completed, so prefer the FHIR path
  for trustworthy numbers.
- **Gemini free-tier quotas** will rate-limit at ~15 RPM. The default
  `--delay 2.0` paces the runner; raise it if you see 429s.
- **`overall_pass`** requires: correct classification, trajectory F1 ≥ 0.75,
  ACR deadline compliance, *and* (for Cat1/Cat2) Communication + Task
  present in audit. Cat3 cases pass when no Communication / Task is
  created.

## Running the unit tests

The scorer logic itself is pure-function and covered by `pytest`:

```bash
pytest eval/tests/ -v
```

No network / agent required for these.
