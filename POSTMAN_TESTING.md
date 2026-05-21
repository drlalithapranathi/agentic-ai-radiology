# Postman Testing Guide

End-to-end test plan for the CritCom agent via Postman.

---

## Setup

### Base URL

| Environment | URL |
|---|---|
| Local docker stack | `http://localhost:8002/` |

> Trailing `/` matters. Drop it and you'll get a 404.

### Common headers

For every request:

| Key | Value |
|---|---|
| `Content-Type` | `application/json` |

If `CRITCOM_REQUIRE_API_KEY=true` in the agent's `.env`:

| Key | Value |
|---|---|
| `X-API-Key` | `<value of CRITCOM_API_KEY>` |

With the default `CRITCOM_REQUIRE_API_KEY=false` no API key header is required.

### Recommended Postman setup

1. **New → Collection** → name it `CritCom`.
2. Collection → **Variables** tab:

   | Variable | Initial value | Current value |
   |---|---|---|
   | `base_url` | `http://localhost:8002` | `http://localhost:8002` |

3. Every request URL: `{{base_url}}/`.
4. Save each request below as a separate item in the collection.

---

## Sanity check (GET, no body)

**Method:** `GET`
**URL:** `{{base_url}}/.well-known/agent-card.json`

Expect a JSON document with non-empty `skills` array. If `skills: []`, the agent is on a stale image — see Troubleshooting in the main README.

---

## Demo A — Cat1 critical finding (FHIR, full pipeline)

**Method:** `POST` · **URL:** `{{base_url}}/`

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "m1",
      "parts": [{"kind": "text", "text": "Process DiagnosticReport dr-001"}]
    }
  }
}
```

**Pass criteria:** `result.history` shows tool chain
`fetch_report_fhir → resolve_provider → dispatch_communication → track_acknowledgment` and `result.status.state == "completed"`.

The Communication and Task get persistent FHIR IDs; final reply mentions Dr. Chen and a 60-min ack window.

---

## Demo B — Cat3 routine finding (agent stops)

```json
{
  "jsonrpc": "2.0",
  "id": "2",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "m2",
      "parts": [{"kind": "text", "text": "Process DiagnosticReport dr-004"}]
    }
  }
}
```

**Pass:** Only `fetch_report_fhir` is called. Final reply: *"ACR category Cat3, no critical communication needed."* No Communication/Task created.

---

## Demo C — DICOM worklist lookup (no findings, agent stops)

```json
{
  "jsonrpc": "2.0",
  "id": "3",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "m3",
      "parts": [{"kind": "text", "text": "Use the DICOM worklist to fetch the study with accession_number ACC0001."}]
    }
  }
}
```

**Pass:** `fetch_report_dicom_tool` returns the worklist entry (patient, modality, priority). No downstream tools because DICOM worklist has no findings → no ACR category → agent stops correctly.

---

## Demo D — Escalation (overdue Task)

```json
{
  "jsonrpc": "2.0",
  "id": "4",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "m4",
      "parts": [{"kind": "text", "text": "Check the acknowledgment status of Task task-overdue-001. If it is overdue and not yet acknowledged, escalate it. The original case was service_request_id sr-002, patient_id patient-002, ACR category Cat2, finding summary: Acute pulmonary emboli involving segmental and subsegmental branches of the right lower lobe pulmonary artery. The escalation timeout should be 1440 minutes."}]
    }
  }
}
```

**Pass:** `track_acknowledgment_tool(action=check)` reports overdue → `escalate_tool` runs → marks old Task failed, dispatches new Communication to on-call Dr. Reyes, opens fresh 24h Task.

---

## Demo E — Audit trail

```json
{
  "jsonrpc": "2.0",
  "id": "5",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "m5",
      "parts": [{"kind": "text", "text": "Use query_audit_tool to return the full Communication and Task history for service_request_id sr-002."}]
    }
  }
}
```

**Pass:** `query_audit_tool` returns every Communication and Task linked to `sr-002`, including the escalation chain from Demo D.

---

## Demo F — DICOM-only end-to-end (worklist + report broker)

The point of this demo: prove the agent can complete the full pipeline from a
DICOM accession number alone, by joining the pristine public DCMTK worklist
data with synthetic findings stored in a separate report-broker layer.

```json
{
  "jsonrpc": "2.0",
  "id": "6",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "m6",
      "parts": [{"kind": "text", "text": "Process accession_number 00007 from the DICOM worklist. Retrieve the signed findings via fetch_radiologist_findings_tool and complete the full critical-results workflow."}]
    }
  }
}
```

**Pass:** Tool chain
```
fetch_report_dicom_tool(00007)            → BLV734623 (Beethoven), urgent
fetch_radiologist_findings_tool(00007)    → text + Cat1 (LLM-classified)
resolve_provider_tool(sr-001)             → Dr. Chen
dispatch_communication_tool(...)          → Communication created in HAPI
track_acknowledgment_tool(create, 60)     → Task created in HAPI
```

`result.status.state == "completed"`. Final reply summarizes the dispatch.

### Stop-path variant (Cat3)

```json
{
  "jsonrpc": "2.0",
  "id": "6b",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "m6b",
      "parts": [{"kind": "text", "text": "Process accession_number 00001 from the DICOM worklist. Use fetch_radiologist_findings_tool to retrieve the signed findings."}]
    }
  }
}
```

**Pass:** `fetch_report_dicom → fetch_radiologist_findings (Cat3)` → agent stops, no Communication/Task created.

---

## Tool-by-tool expected calls

For each demo, here's exactly which tools should fire (and which should NOT):

| Demo | Should fire | Should NOT fire |
|---|---|---|
| A | `fetch_report_fhir`, `resolve_provider`, `dispatch_communication`, `track_acknowledgment` | `fetch_radiologist_findings`, `escalate` |
| B | `fetch_report_fhir` | everything else |
| C | `fetch_report_dicom` | everything else |
| D | `track_acknowledgment(check)`, `escalate`, `dispatch_communication`, `track_acknowledgment(create)` | `fetch_report_*` |
| E | `query_audit` | everything else |
| F | `fetch_report_dicom`, `fetch_radiologist_findings`, `resolve_provider`, `dispatch_communication`, `track_acknowledgment` | `escalate` |
| F (stop) | `fetch_report_dicom`, `fetch_radiologist_findings` | everything else |

If you see tools fire that shouldn't, or miss tools that should — that's a regression.

---

## Reading the response

A successful response looks like:

```json
{
  "id": "<your request id>",
  "jsonrpc": "2.0",
  "result": {
    "id": "<task uuid>",
    "contextId": "<session uuid>",
    "kind": "task",
    "status": { "state": "completed", "timestamp": "..." },
    "artifacts": [
      { "parts": [{"kind": "text", "text": "<final natural-language summary>"}] }
    ],
    "history": [ /* full step-by-step trace */ ],
    "metadata": { "adk_usage_metadata": { "totalTokenCount": 2097, ... } }
  }
}
```

To see what tools fired, walk `result.history` and look for parts where
`metadata.adk_type == "function_call"` (the args the LLM passed) and
`metadata.adk_type == "function_response"` (what the tool returned).

---

## Verifying side effects in HAPI

After Demos A, D, F — confirm the FHIR resources actually persisted:

```bash
# Communications written by dispatch_communication
curl http://localhost:8081/fhir/Communication?based-on=ServiceRequest/sr-001

# Tasks written by track_acknowledgment
curl http://localhost:8081/fhir/Task?focus=Communication/<id-from-response>
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `501 Not Implemented` | Agent fell back to stub — `a2a-sdk` not installed in container. Rebuild with the pin in `requirements.txt`. |
| `[Errno -2] Name or service not known` | Agent container can't reach `hapi-fhir`. Confirm both containers are on the same Docker network. |
| `503 UNAVAILABLE` | Gemini model overloaded — retry, or set `CRITCOM_LLM_MODEL=gemini-2.5-flash-lite`. |
| `429 RESOURCE_EXHAUSTED` | Gemini free-tier daily quota hit. Use a different key. |
| Final reply hallucinates findings on DICOM-only path | Agent skipped `fetch_radiologist_findings` — check system prompt is the updated version. |
| `agent-card.json` returns `skills: []` | Stale image. `docker-compose build critcom-agent && docker rm -f critcom-agent && docker-compose up -d critcom-agent`. |
