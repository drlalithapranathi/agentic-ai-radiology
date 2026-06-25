# CritCom

**Critical Results Communication Agent** — an A2A-compatible, FHIR-native AI
agent that automates the radiology critical-results workflow, wired into a real
DICOM viewer and a priority-sorted reading worklist.

When a radiologist signs a finding, CritCom classifies its urgency with Gemini,
resolves the ordering physician, records the notification, opens an
acknowledgment timer, and escalates to on-call if it goes unanswered — every
step written to the patient chart as FHIR resources.

## The integration

CritCom is not just an agent endpoint — it is the full clinical loop, assembled
from four services on one network:

```
  EHR order ─► DICOM Modality Worklist (Orthanc)
                     │
                     ▼
   ┌─────────────────────────────────────────────┐
   │  Streamlit UI  ── priority worklist + inbox   │   ◄── radiologist triages & signs
   │  Orthanc viewer ── "Send to CritCom" button   │   ◄── or signs from the DICOM viewer
   └───────────────────────┬──────────────────────┘
                           │  A2A JSON-RPC (message/send, X-API-Key)
                           ▼
                  CritCom Agent  (Google ADK + Gemini 2.5 Pro / Vertex AI)
                           │  tool-calling loop
                           ▼
                     FHIR R4 (HAPI)  ── Communication, Task, Practitioner
                           │
                           ▼
              Tracking inbox in the UI  (live from FHIR)
```

**Two radiologist entry points, one backend.** A signed finding can be sent from
the **Streamlit worklist** or from a **"Send to CritCom" button inside the
Orthanc DICOM viewer** (a Python Orthanc plugin, `deploy/orthanc/`). Both call
the same agent and write to the same FHIR server, so both surface in the same
live tracking inbox.

## End to end — each step and the API call it makes

| Step | What happens | API call |
|---|---|---|
| 1. Order intake | An order becomes a DICOM worklist entry carrying its priority (EMERGENCY/STAT/HIGH/ROUTINE). | DICOM MWL entry in Orthanc |
| 2. Triage | The UI loads the worklist live and sorts by priority. | DICOM **C-FIND** → Orthanc (4242) |
| 3. View study | The radiologist opens the CT. | Orthanc REST: `POST /tools/find`, `GET /instances/{id}/preview` |
| 4. Sign & Send | Signed findings fire the agent (from UI or viewer). | **A2A JSON-RPC** `POST /` `message/send` |
| 5. Classify | Gemini infers the ACR category (Cat1/Cat2/Cat3). | Vertex AI `generateContent` (Gemini) |
| 6. Resolve physician | Find the ordering provider. | FHIR `GET /ServiceRequest`, `GET /Practitioner` |
| 7. Notify | Record the critical-result notification (Cat1/Cat2); Cat3 logs a routine "no alert" entry. | FHIR `POST /Communication` |
| 8. Track | Open an acknowledgment timer (60 min Cat1 / 24 h Cat2). | FHIR `POST /Task` |
| 9. Escalate | If unacknowledged in time, notify on-call. | FHIR `POST /Communication` + `POST /Task` |
| 10. Inbox | The UI shows every tracked result and its ack status. | FHIR `GET /Communication`, `GET /Task` |

The LLM only decides *which tool to call next* and *which ACR category applies* —
every fact written to FHIR comes from a deterministic tool, never invented by the
model. The worklist priority (`RequestedProcedurePriority`) is a DICOM tag set at
order time; the ACR category is inferred by Gemini from the signed findings.

## The agent's tools

The agent (Google ADK + Gemini) exposes 8 tools and decides which to call:

| Tool | What it does |
|---|---|
| `fetch_report_fhir` | Get a signed DiagnosticReport + ServiceRequest from FHIR |
| `fetch_report_dicom` | C-FIND against a DICOM worklist (fallback when no FHIR) |
| `fetch_radiologist_findings` | Read signed findings, then LLM-classify the ACR category |
| `resolve_provider` | Walk ServiceRequest → ordering / on-call Practitioner |
| `dispatch_communication` | Write a FHIR `Communication` (the notification record) |
| `track_acknowledgment` | Create / check / complete a FHIR `Task` for ack tracking |
| `escalate` | Mark an overdue Task failed, notify on-call, open a new Task |
| `query_audit` | Return the full Communication + Task history for a case |

## Components

| Service | Role | Path |
|---|---|---|
| **Agent** | A2A endpoint, ADK + Gemini tool loop | `src/critcom/` |
| **Streamlit UI** | Priority worklist, sign flow, tracking inbox | `ui/` |
| **Orthanc plugin** | "Send to CritCom" button in the classic Explorer | `deploy/orthanc/` |
| **HAPI FHIR** | R4 store for Communication / Task / Practitioner | container |
| **Orthanc** | DICOM store + Modality Worklist SCP | container |

## Quick start

```bash
echo 'GOOGLE_API_KEY=AIza...' > .env     # free key: https://aistudio.google.com/apikey
docker compose up -d                     # agent + HAPI (FHIR auto-seeded) + Orthanc
```

The agent listens on **`http://localhost:8002`**. Check it's up:

```bash
curl http://localhost:8002/.well-known/agent-card.json
```

Run the full pipeline on a seeded Cat1 case:

```bash
curl -X POST http://localhost:8002/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m1",
         "parts":[{"kind":"text","text":"Process DiagnosticReport dr-001"}]}}}'
# → classifies a Type A aortic dissection (Cat1), notifies Dr. Chen,
#   opens an acknowledgment Task with the 60-min Cat1 deadline.
```

### Seed the worklist (for the priority UI + DICOM scenarios)

Only FHIR is auto-seeded. To populate the priority-sorted worklist and the CT
images (single source of truth: `src/critcom/scripts/_demo_data.py`):

```bash
pip install -e .
critcom-seed-dicom          # writes priority-tagged worklist entries
critcom-seed-images         # uploads one distinct CT per order
```

### Run the UI

```bash
docker build -t critcom-ui:local ui
docker run -d --network <compose-net> -p 8501:8501 \
  -e CRITCOM_UI_AGENT_URL=http://critcom-agent:8001 \
  -e CRITCOM_UI_FHIR_URL=http://hapi-fhir:8080/fhir \
  -e CRITCOM_UI_ORTHANC_URL=http://orthanc:8042 \
  critcom-ui:local
# → priority worklist + live tracking inbox at http://localhost:8501
```

> **Production note.** The hosted demo runs the orchestrator on **Vertex AI**
> (Gemini 2.5 Pro); the local quick-start uses a free `GOOGLE_API_KEY`. Set
> `CRITCOM_REQUIRE_API_KEY=true` and an `X-API-Key` for any non-local deployment.

## How classification works

Classification is done by **Gemini**, not a lookup table. The signed findings text
is sent to the model (temperature 0, JSON output) which returns the ACR category,
the key finding, reasoning, and a confidence score (`src/critcom/classification/`).
Cat1 = immediate (60 min), Cat2 = urgent (24 h), Cat3 = routine (logged, no alert).

## Docs

- **[CritCom_API_and_Workflow.docx](CritCom_API_and_Workflow.docx)** — one-page step → API-call map.
- **[DOCKER.md](DOCKER.md)** — service diagram, container vs. host addresses, ops.
- **[eval/README.md](eval/README.md)** — the 5-metric performance harness.
- **[POSTMAN_TESTING.md](POSTMAN_TESTING.md)** — full set of demo requests.

## Testing & evaluation

- **Unit tests** — `pytest` runs the suite with no network or LLM calls
  (the FHIR client is mocked with `respx`); `pytest -m llm` adds the live-LLM
  classifier tests (needs `GOOGLE_API_KEY`).
- **Performance eval** — a 5-metric harness (ACR accuracy, trajectory F1,
  FHIR state validity, deadline compliance, pass^k) in `eval/`. Run with
  `docker compose --profile eval run --rm critcom-eval`. See [eval/README.md](eval/README.md).

## License

MIT