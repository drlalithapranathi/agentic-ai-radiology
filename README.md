# CritCom

> **Critical Results Communication Agent** — an A2A-compatible, FHIR-native AI
> agent that automates the radiology critical-results workflow.

CritCom routes signed `DiagnosticReport` resources (or DICOM worklist
entries) to the right ordering physician, tracks acknowledgment as FHIR
`Task` resources, and escalates to the on-call backup if no response is
received within the ACR-defined timeframe.

---

## Try it

Bring the stack up locally (`docker compose up -d`) and the agent listens
at **`http://localhost:8002`**.

```bash
# 1. Read the public agent card (A2A discovery)
curl http://localhost:8002/.well-known/agent-card.json
```

Then run any of the demo scenarios below. Each is a single curl that produces
a complete tool trace plus a natural-language summary in the response.

### Demo scenarios

```bash
# A. Cat1 critical finding — full pipeline (fetch → resolve → dispatch → track)
curl -X POST http://localhost:8002/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m1",
         "parts":[{"kind":"text","text":"Process DiagnosticReport dr-001"}]}}}'
# → fetches Type A aortic dissection, dispatches to Dr. Chen,
#   opens Task with 60-min Cat1 deadline.

# B. Cat3 routine finding — agent should STOP (no critical comm needed)
curl -X POST http://localhost:8002/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"2","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m2",
         "parts":[{"kind":"text","text":"Process DiagnosticReport dr-004"}]}}}'
# → "ACR category Cat3, no critical communication needed."

# C. DICOM fallback path — query the modality worklist
curl -X POST http://localhost:8002/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"3","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m3",
         "parts":[{"kind":"text","text":"Use the DICOM worklist to fetch the study with accession_number ACC0001."}]}}}'
# → returns full study metadata via DICOM C-FIND against Orthanc.

# D. Escalation — ack timer expired, agent escalates to on-call
curl -X POST http://localhost:8002/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"4","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m4",
         "parts":[{"kind":"text","text":"Check the acknowledgment status of Task task-overdue-001. If it is overdue and not yet acknowledged, escalate it. The original case was service_request_id sr-002, patient_id patient-002, ACR category Cat2, finding summary: Acute pulmonary emboli involving segmental and subsegmental branches of the right lower lobe pulmonary artery. The escalation timeout should be 1440 minutes."}]}}}'
# → marks the old Task failed, dispatches a new Communication
#   to on-call Dr. Reyes, opens a fresh 24h Task.

# E. Audit trail — full Communication + Task history for a case
curl -X POST http://localhost:8002/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"5","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m5",
         "parts":[{"kind":"text","text":"Use query_audit_tool to return the full Communication and Task history for service_request_id sr-002."}]}}}'
# → returns every Communication and Task linked to that case.

# F. Pure DICOM-only end-to-end — worklist + findings bridge
curl -X POST http://localhost:8002/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"6","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m6",
         "parts":[{"kind":"text","text":"Process accession_number 00007 from the DICOM worklist. Retrieve the signed findings via fetch_radiologist_findings_tool and complete the full critical-results workflow."}]}}}'
# → fetches DCMTK Beethoven worklist entry, pulls signed findings from the
#   report-broker fixture, LLM classifies as Cat1, dispatches to Dr. Chen,
#   opens 60-min ack Task. Worklist data itself stays pristine.
```

### Seed data available for demos

| ID | Patient | Finding | ACR | Used in |
|---|---|---|---|---|
| `dr-001` | Robert Kowalski | Type A aortic dissection | Cat1 | A |
| `dr-002` | Linh Nguyen | Subsegmental PE | Cat2 | (tied to escalation D/E) |
| `dr-003` | Dorothy Williams | Hypertensive ICH | Cat1 | (Cat1 alt) |
| `dr-004` | Eleanor Goldberg | Stable cholelithiasis | Cat3 | B |
| `ACC0001`–`ACC0003` | (DICOM worklist) | scheduled CT | n/a | C |
| `00001`–`00010` | DCMTK fixtures (Vivaldi, Beethoven, Mozart, Haydn) | scheduled, no findings | n/a | F (worklist side) |
| `00007.json` | Beethoven (synthetic findings file) | Saddle PE w/ RV strain | Cat1 (LLM) | F |
| `00003.json` | Vivaldi (synthetic findings file) | Subsegmental PE, no RV strain | Cat2 (LLM) | F (Cat2 variant) |
| `00001.json` | Vivaldi (synthetic findings file) | Stable cholelithiasis | Cat3 (LLM) | F (stop path) |
| `task-overdue-001` | (overdue ack for sr-002) | — | — | D, E |

---

## Run the full stack locally with Docker

```bash
echo 'GOOGLE_API_KEY=AIza...' > .env
docker compose up -d                           # FHIR + DICOM + seed + agent
curl http://localhost:8002/.well-known/agent-card.json
```

The compose stack auto-seeds HAPI on every `up` (the `seed-fhir` service
is idempotent), so you never have to hand-curl bundles. The agent reaches
HAPI / Orthanc by **docker service name** (`hapi-fhir`, `orthanc`) — never
`localhost`. See **[DOCKER.md](DOCKER.md)** for the full service diagram
and why every URL is a service name, not localhost.

## Performance evaluation

A separate eval harness lives under `eval/`. It runs 5 metrics against
the agent — ACR classification, trajectory F1, FHIR state validity,
deadline compliance, and pass^k reliability — all grounded in published
2025–2026 clinical-agent benchmarks (TRAJECT-Bench, FHIR-AgentEval,
τ-bench, ART). Run it with:

```bash
docker compose --profile eval run --rm critcom-eval
```

Reports drop into `eval/reports/eval-<UTC>.{md,json}`. See `eval/README.md`
for the full metric list and how to extend the labeled-case fixture.

## Architecture — what runs where

The full stack is four Docker services brought up by `docker compose up`:

```
                 Clients (curl, Postman, another A2A agent)
                     │
                     │  http://localhost:8002
                     ▼
        ┌────────────────────────────────────────┐
        │  critcom-agent  (Docker container)     │  ← the AI agent
        │  ADK + Gemini, listens on :8001 inside │    (8 tools,
        │  the container, mapped to host :8002   │     A2A JSON-RPC)
        └────────┬───────────────────┬───────────┘
                 │                   │
                 │  docker network   │  docker network
                 ▼                   ▼
        ┌──────────────────┐  ┌──────────────────┐
        │  critcom-hapi    │  │  critcom-orthanc │
        │  HAPI FHIR R4    │  │  DICOM + worklist│
        │  :8080 internal  │  │  :8042 + :4242   │
        │  (host :8081)    │  │  (host :8042)    │
        └──────────────────┘  └──────────────────┘
                 ▲
                 │  POST seed_bundle.json (one-shot, idempotent)
                 │
        ┌────────┴───────────┐
        │  critcom-seed-fhir │  curl image, exits 0 after seeding HAPI
        └────────────────────┘
```

**Plain-English version:**

- The **agent** is the only thing clients talk to.
- **HAPI** is the agent's private FHIR R4 store. The agent reaches it on the
  internal docker network at `http://hapi-fhir:8080/fhir` — never `localhost`.
- **Orthanc** is the private DICOM store + Modality Worklist. Internal only.
- **seed-fhir** runs once on every `docker compose up`, POSTs the seed bundle
  to HAPI, exits clean. Idempotent — re-runs are safe.

For TLS / a public domain, terminate at a reverse proxy of your choice (nginx,
Caddy, Traefik) on the host — out of scope for this repo.

### Container-internal vs. host addresses

| Address | Used by |
|---|---|
| `http://localhost:8002/` | Clients on the host (your machine, your reverse proxy) |
| `http://critcom-agent:8001` | The eval harness from inside the docker network |
| `http://hapi-fhir:8080/fhir` | The agent, from inside the docker network |
| `http://orthanc:8042` | The agent, from inside the docker network |
| `http://localhost:8081/fhir` | Manual debugging from the host |

Containers refer to each other by **service name** (`hapi-fhir`, `orthanc`,
`critcom-agent`) — that's why the agent's `.env` says
`CRITCOM_FHIR_BASE_URL=http://hapi-fhir:8080/fhir` and not `localhost`. See
[DOCKER.md](DOCKER.md) for the full wrong/right cheat-sheet.

---

## What happens when you call the agent

A worked example: you POST `"Process DiagnosticReport dr-001"` to the agent.

```
Client
        │  POST http://localhost:8002/
        │  body: A2A JSON-RPC "message/send"
        ▼
nginx → critcom-agent
        │
        │  ADK runtime hands the message to the LLM (Gemini 2.5 Flash Lite)
        │  along with the system prompt and the 7 available tools.
        ▼
LLM decides: "I need to fetch the report first."
        │
        ▼
TOOL 1: fetch_report_fhir_tool({"diagnostic_report_id": "dr-001"})
        │
        │  → Agent calls HAPI: GET http://hapi-fhir:8080/fhir/DiagnosticReport/dr-001
        │  → HAPI returns the resource
        │  → Tool normalizes it into a CritComStudy:
        │       { acr_category: "Cat1",
        │         service_request_id: "sr-001",
        │         patient_id: "patient-001",
        │         report_text: "TYPE A AORTIC DISSECTION ..." }
        │
        ▼
LLM sees acr_category = "Cat1" → critical, must continue.
        │
        ▼
TOOL 2: resolve_provider_tool({"service_request_id": "sr-001"})
        │
        │  → Agent calls HAPI: GET ServiceRequest/sr-001
        │  → Reads .requester → Practitioner/practitioner-001
        │  → Fetches that Practitioner + their PractitionerRole
        │  → Returns: Dr. Michael Wei Chen, phone, pager, email
        │
        ▼
TOOL 3: dispatch_communication_tool({...})
        │
        │  → Agent BUILDS a FHIR Communication resource:
        │       status="in-progress", category="Cat1",
        │       subject=Patient/patient-001,
        │       about=ServiceRequest/sr-001,
        │       recipient=Practitioner/practitioner-001,
        │       sent=<now>, payload=<finding text>
        │  → Agent POSTs it to HAPI: POST /Communication
        │  → HAPI assigns ID 1017 and persists to disk
        │  → Tool returns: {communication_id: "1017", sent: "..."}
        │
        ▼
TOOL 4: track_acknowledgment_tool({"action": "create",
                                    "communication_id": "1017",
                                    "timeout_minutes": 60, ...})
        │
        │  → Agent BUILDS a FHIR Task:
        │       status="requested",
        │       focus=Communication/1017,
        │       owner=Practitioner/practitioner-001,
        │       restriction.period.end=<now + 60 minutes>
        │  → Agent POSTs it to HAPI: POST /Task
        │  → HAPI assigns ID 1018 and persists
        │  → Tool returns: {task_id: "1018", deadline: "..."}
        │
        ▼
LLM sees all four tools succeeded.
        │
        ▼
Agent responds with a natural-language summary of what it did,
plus the full machine-readable history of every tool call and return.
```

### What you'd see in HAPI afterwards

Two new resources, persistent on the VM disk:

```bash
# The notification record
curl http://localhost:8081/fhir/Communication/1017
#  → category Cat1, recipient practitioner-001, payload = the finding text

# The acknowledgment-tracking task with the 60-minute Cat1 deadline
curl http://localhost:8081/fhir/Task/1018
#  → status requested, owner practitioner-001,
#     restriction.period.end = sent + 60 min
```

If 60 minutes pass without acknowledgment, the next call to the agent
(`"Check Task 1018"`) would invoke `track_acknowledgment_tool` with
`action="check"`, see the deadline has passed, and call `escalate_tool` —
which marks Task 1018 failed, resolves the on-call provider
(`practitioner-oncall-001`), and creates a fresh Communication + Task
for them.

### Where the LLM fits

The LLM **does not** invent medical facts, IDs, or contact info. It only
decides *which tool to call next* based on the data the previous tool
returned. Every fact in the final FHIR record came from a deterministic
tool reading FHIR/DICOM. The LLM is the dispatcher; the tools are the truth.

---

## Local development

### 1. Clone and configure

```bash
git clone https://github.com/iupui-soic/agentic-ai-radiology.git
cd agentic-ai-radiology
cp .env.example .env
```

Edit `.env`:
- Set `GOOGLE_API_KEY` (free key from https://aistudio.google.com/apikey).
- Set `CRITCOM_LLM_MODEL=gemini-2.5-flash-lite` — the default `gemini-2.0-flash`
  is in a free-tier quota bucket that gets exhausted fast.
- Leave `CRITCOM_REQUIRE_API_KEY=false` for local dev.

### 2. Start the stack

```bash
docker compose up --build -d
```

This starts:
| Service | Host port | Container port |
|---|---|---|
| HAPI FHIR | `:8081` | `:8080` |
| Orthanc REST/UI | `:8042` | `:8042` |
| Orthanc DICOM | `:4242` | `:4242` |
| CritCom agent | `:8002` | `:8001` |

> Host ports `8081`/`8002` are deliberately offset from defaults to avoid
> collisions on the deploy VM. Inside the docker network the agent still talks
> to HAPI on `:8080` — only host-side ports are remapped.

### 3. Install the Python package and seed data

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .

critcom-seed         # 17 FHIR resources into HAPI
critcom-seed-dicom   # synthetic worklist entries into Orthanc
```

### 4. Verify

```bash
# Agent card (note port 8002, not 8001)
curl http://localhost:8002/.well-known/agent-card.json

# FHIR
curl http://localhost:8081/fhir/Patient/patient-001
curl http://localhost:8081/fhir/DiagnosticReport/dr-001

# Run the agent end-to-end
curl -X POST http://localhost:8002/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m1",
         "parts":[{"kind":"text","text":"Process DiagnosticReport dr-001"}]}}}'

# Orthanc UI (login: orthanc / orthanc)
open http://localhost:8042
```

### 5. Run the test suite

```bash
pytest -v             # 45 tests, no LLM calls
pytest -v -m llm      # +2 tests that hit the real LLM (needs GOOGLE_API_KEY)
```

---

## Project layout

```
critcom/
├── critcom_agent/           # ADK Agent — instruction, tool wiring, A2A app
│   ├── agent.py
│   └── app.py
├── shared/                  # po-adk-python style infrastructure
│   ├── middleware.py        # X-API-Key auth + A2A metadata bridging
│   ├── fhir_hook.py         # before_model_callback that extracts FHIR ctx
│   ├── app_factory.py       # create_a2a_app()
│   ├── logging_utils.py
│   └── tools/               # ADK-compatible wrappers around critcom.tools
├── src/critcom/
│   ├── fhir/                # Pydantic FHIR R4 models + async client
│   ├── classification/      # ACR classifier (Gemini-backed)
│   ├── tools/               # 8 tools — the actual logic
│   └── scripts/             # seed.py (HAPI), seed_dicom*.py (Orthanc, 3 variants)
├── tests/
│   ├── fixtures/
│   │   ├── fhir/seed_bundle.json
│   │   ├── reports/sample_reports.json
│   │   └── dicom_findings/  # report-broker JSON keyed by DICOM accession
│   └── test_*.py
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## The 8 tools

| Tool | What it does |
|---|---|
| `fetch_report_fhir` | Get a signed DiagnosticReport + linked ServiceRequest from FHIR |
| `fetch_report_dicom` | C-FIND query against a DICOM MWL (fallback when no FHIR) |
| `fetch_radiologist_findings` | Read radiologist-signed findings from the local report broker (keyed by DICOM accession), then auto-classify via the LLM. Bridges the DICOM-only path to the rest of the workflow. |
| `resolve_provider` | Walk ServiceRequest → Practitioner / on-call PractitionerRole |
| `dispatch_communication` | Create a FHIR `Communication` resource — notification record |
| `track_acknowledgment` | Create / check / complete a FHIR `Task` for ack tracking |
| `escalate` | Mark overdue Task failed, notify on-call, create new Task |
| `query_audit` | Return full Communication + Task history for a case |

> "Notification" today means a FHIR `Communication` resource is written to HAPI.
> No SMS/page/email integration yet — the audit trail is the deliverable.

---

## Workflow

```
1. Trigger
   ├── FHIR: new DiagnosticReport status="final"
   └── DICOM: completed worklist entry

2. fetch_report_{fhir,dicom}
   └── Returns CritComStudy { priority, acr_category, IDs, text }
       FHIR  → has report_text; acr_category from tag (or LLM if tag missing)
       DICOM → scheduling only; no findings, no acr_category → goto 2b

2b. (DICOM path only) fetch_radiologist_findings
    └── Reads tests/fixtures/dicom_findings/<accession>.json
        Runs LLM classifier on the report text → fills acr_category.
        Returns service_request_id + patient_id for downstream tools.
        If no findings file → report not signed yet, stop.

3. Read ACR category
   └── Cat3 / None  →  log only, stop
       Cat1 / Cat2  →  continue

4. resolve_provider
   └── ServiceRequest.requester → Practitioner contact

5. dispatch_communication  →  FHIR Communication
6. track_acknowledgment    →  FHIR Task with deadline
7. If timeout: escalate    →  on-call provider, new Task
```

### Why the report broker is separate

DICOM Modality Worklist data is *scheduling* — the order, not the report. In
real hospitals the worklist comes from RIS and the signed report comes from
the dictation/reporting system. This codebase keeps that separation: the
worklist (`orthanc-worklists/*.wl`, including DCMTK public fixtures) stays
pristine and citable, while findings live in their own keyed-by-accession
directory (`tests/fixtures/dicom_findings/<accession>.json`). They join on
`accession_number`. To add a demoable Cat1/Cat2 case for any DCMTK
accession, drop a JSON file in that directory; nothing else changes.

`ServiceRequest.priority` (FHIR) and `Requested Procedure Priority` (DICOM)
control the **agent processing queue order** only. The clinical urgency (ACR
category) comes from the report itself.

---

## Configuration

All settings live in `.env`. The most important ones:

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` | Gemini model (free tier from AI Studio) |
| `CRITCOM_LLM_MODEL` | Use `gemini-2.5-flash-lite` (others are quota-exhausted) |
| `CRITCOM_API_KEY` | Shared-secret API key callers send in the `X-API-Key` header |
| `CRITCOM_REQUIRE_API_KEY` | Set `false` for local dev / open demo |
| `CRITCOM_FHIR_BASE_URL` | HAPI FHIR base URL |
| `CRITCOM_DICOM_HOST` / `_PORT` / `_AET` | Orthanc DICOM endpoint |
| `CRITCOM_FHIR_EXTENSION_URI` | A2A metadata extension URI for FHIR context |
| `CRITCOM_FINDINGS_DIR` | Report-broker dir for `fetch_radiologist_findings` (default `tests/fixtures/dicom_findings`) |
| `CRITCOM_CAT1_ACK_TIMEOUT_MINUTES` | ACR Cat1 ack deadline (default 60) |
| `CRITCOM_CAT2_ACK_TIMEOUT_MINUTES` | ACR Cat2 ack deadline (default 1440) |

---

## Deployment

The same `docker-compose.yml` that runs locally runs on any host with Docker:

```bash
ssh <user>@<your-host>
cd <repo>
git pull origin main
docker compose build critcom-agent
docker compose up -d
```

For a public deployment, terminate TLS at a reverse proxy of your choice
(nginx, Caddy, Traefik) and forward to `http://localhost:8002`.

---

## Troubleshooting

**`KeyError: 'ContainerConfig'` on `docker-compose up`**
Bug in docker-compose v1.29.2 against newer Docker daemons (BuildKit images).
Workaround: `docker rm -f critcom-agent` then `docker-compose up -d
critcom-agent`. Affects the VM only — Docker Desktop on Mac uses compose v2.

**`429 RESOURCE_EXHAUSTED` from Gemini**
The free-tier daily request quota for `gemini-2.0-flash` and
`gemini-2.0-flash-lite` is `0` on most personal API keys. Set
`CRITCOM_LLM_MODEL=gemini-2.5-flash-lite` and restart the agent container.

**`503 UNAVAILABLE` from `gemini-2.5-flash`**
Transient — the model is overloaded. Retry, or fall back to
`gemini-2.5-flash-lite`.

**`Object of type datetime is not JSON serializable`**
Fixed in commit `ca294b3` (FHIR client `model_dump` calls switched to
`mode="json"`). Pull and rebuild if you see it.

**Agent card returns `skills: []`**
You're on a stale image. Rebuild: `docker-compose build critcom-agent` then
the rm-then-up workaround above. Container code is COPYed at build time, not
bind-mounted — `restart` alone won't pick up code changes.

---

## Roadmap / possible enhancements

Things that would make the system feel more production-grade. None are required
for the current submission scope.

- **Real DICOM images from TCIA.** Pull a handful of anonymized chest CTs from
  [The Cancer Imaging Archive](https://www.cancerimagingarchive.net/) and push
  them into Orthanc as completed studies, with the synthetic `.wl` worklist
  entries updated to reference those real study UIDs. Adds visual flair for
  demos. Note: Modality Worklist data itself is per-hospital operational state
  and isn't publicly distributed, so the synthetic worklist generation has to
  stay regardless.
- **Synthea-generated patients.** Use [Synthea](https://github.com/synthetichealth/synthea)
  to seed 50–100 synthetic patients with full longitudinal FHIR histories
  (demographics, prior conditions, meds). Lets the agent operate against a
  realistic-sized chart, not just 4 curated patients.
- **Modality variety.** Today all seed cases are CT. Add an MRI brain (acute
  stroke alert) and an ultrasound (ruptured AAA) to demonstrate the agent
  isn't modality-specific.
- **Real notification channels.** `dispatch_communication` writes a FHIR
  `Communication` today but doesn't actually send anything. Wire in Twilio
  (SMS), a hospital paging API, or Direct secure email so notifications
  reach the recipient out-of-band. The audit trail already exists; this is
  purely a transport layer.
- **Outcome metrics dashboard.** Time-to-acknowledgment, escalation rate by
  category, by service line, etc. — all derivable from the existing FHIR
  Communication + Task records via standard FHIR search.
- **Acknowledgment via inbound A2A.** Today the only way to mark a Task
  completed is calling `track_acknowledgment(action=mark_acknowledged)`
  through the agent. A small inbound webhook (or a "respond" message from
  the recipient's own agent) would close the loop end-to-end.

---

## License

MIT