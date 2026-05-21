# CritCom — Docker layout

> **Rule of thumb: nothing on `localhost`.** Every component below runs in a
> docker container and talks to its neighbors over docker's internal DNS
> (service names). If you ever find yourself typing `localhost:8081` from
> inside a container, that's a bug — fix it.

## Services and who talks to who

```
                              ┌────────────────────┐
                              │  critcom-eval      │   profile: "eval"
                              │  (Dockerfile:      │   on-demand only
                              │   eval/Dockerfile) │
                              └─────────┬──────────┘
                                        │ POST /
                                        ▼
┌──────────────────────────────────────────────────────┐
│  critcom-agent  (port 8001 in container, 8002 host)  │
│                                                      │
│   FHIR client  ──▶  http://hapi-fhir:8080/fhir       │
│   DICOM client ──▶  orthanc:4242 (C-FIND)            │
│   Orthanc REST ──▶  http://orthanc:8042              │
└──────────┬─────────────────────────┬─────────────────┘
           │                         │
           ▼                         ▼
   ┌──────────────┐          ┌──────────────┐
   │  hapi-fhir   │          │   orthanc    │
   │  (HAPI R4)   │          │  (DICOM +    │
   │              │          │   worklist)  │
   │ host: 8081   │          │ host: 8042   │
   │ ctr : 8080   │          │ ctr : 8042   │
   └──────┬───────┘          └──────────────┘
          ▲
          │ POST seed_bundle.json (one-shot)
          │
   ┌──────┴───────┐
   │ seed-fhir    │  curl image, exits 0 after seeding
   │ (idempotent) │  runs before critcom-agent comes up
   └──────────────┘
```

## Service summary

| Service | Image / Build | Host port | Container port | Purpose |
|---|---|---|---|---|
| `hapi-fhir` | `hapiproject/hapi:latest` | `8081` | `8080` | FHIR R4 server (Communication / Task / etc.) |
| `orthanc` | `orthancteam/orthanc:latest` | `8042` (REST), `4242` (DICOM) | same | DICOM store + Modality Worklist |
| `seed-fhir` | `curlimages/curl:latest` | none | none | One-shot: POSTs `tests/fixtures/fhir/seed_bundle.json` to HAPI, exits |
| `critcom-agent` | `./Dockerfile` | `8002` | `8001` | The CritCom A2A agent (ADK + Gemini) |
| `critcom-eval` | `./eval/Dockerfile` | none | none | Performance harness; runs `python -m eval` against the agent (profile `eval`) |

## Quick start

```bash
# 0. Set your Gemini API key
echo 'GOOGLE_API_KEY=AIza...' > .env

# 1. Bring up FHIR, DICOM, seed, agent (one command — seed runs once, then agent starts)
docker compose up -d

# 2. Confirm the agent is healthy
curl -sS http://localhost:8002/.well-known/agent-card.json | jq .skills[0].name
# → "Process critical radiology finding"

# 3. Send a demo request
curl -sS -X POST http://localhost:8002/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m1",
         "parts":[{"kind":"text","text":"Process DiagnosticReport dr-001"}]}}}'
```

## Running the eval harness

The eval service is gated behind the `eval` profile so it doesn't fire on
every `docker compose up` (it makes LLM calls and burns quota).

```bash
# Run the full eval against the in-network agent
docker compose --profile eval run --rm critcom-eval

# Skip the query_audit follow-up (faster, no state validation)
docker compose --profile eval run --rm critcom-eval --no-audit

# Run with pass^3 reliability
docker compose --profile eval run --rm critcom-eval --k 3

# Target a remote agent instead of the local stack
docker compose --profile eval run --rm \
  -e CRITCOM_EVAL_BASE_URL=http://<your-agent-host>:<port> \
  critcom-eval

# Run only one labeled case
docker compose --profile eval run --rm critcom-eval --case fhir-cat1-aortic-dissection
```

Reports land under `./eval/reports/eval-<UTC>.{md,json}` on the host
(mounted volume). The `.gitignore` keeps these out of git by default.

## Common mistakes (and the right answer)

| Wrong | Right | Why |
|---|---|---|
| `CRITCOM_FHIR_BASE_URL=http://localhost:8081/fhir` | `CRITCOM_FHIR_BASE_URL=http://hapi-fhir:8080/fhir` | Inside the agent container, `localhost` is the container itself, not the host. Use the docker service name. |
| `python -m eval` from your laptop | `docker compose --profile eval run --rm critcom-eval` | The eval container has the same env every time. No "works on my machine." |
| Hand-curl seed bundles via SSH | `docker compose up` (seeds automatically) | Idempotent + reproducible + zero manual steps. |
| Restart `hapi-fhir` and re-curl seed by hand | Just `docker compose up -d seed-fhir` again | The seed service is repeatable. |

## Deploying to a remote host

On any host with docker installed:

```bash
ssh <user>@<your-host>
cd <repo>
git fetch origin main
git merge origin/main          # bring in the new compose + eval service

# Stop whatever is running and bring up the clean stack
docker compose down
docker compose up -d --build
```

Or, simpler: copy this docker-compose.yml + eval/ tree from the laptop
repo and rebuild. The seed will re-populate HAPI automatically.
