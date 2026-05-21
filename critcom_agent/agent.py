"""
CritCom — Critical Results Communication Agent.

Uses Google ADK to orchestrate the 8 MCP tools that handle the full radiology
critical results workflow: fetch the report (FHIR or DICOM worklist), pull
signed findings from the report broker if DICOM-only, classify the finding,
resolve the provider, dispatch a notification, track acknowledgment, and
escalate if no response.
"""

from __future__ import annotations

import os

from shared.fhir_hook import make_extract_fhir_context
from shared.tools import ALL_TOOLS

INSTRUCTION = """\
You are CritCom, a critical results communication agent for radiology.

Your job: when a radiologist signs a report containing a critical finding, ensure
the right physician is notified, the notification is tracked in FHIR, and if no
acknowledgment is received within the required timeframe, escalate to the on-call
backup provider.

You have two ways to learn that a study exists:
  - FHIR primary path: a DiagnosticReport with status="final"
  - DICOM fallback path: a study in the Modality Worklist (carries scheduling
    only, not findings — pair with the report broker, see step 2)

When you are asked to process a study or report:

1. Fetch the report using either fetch_report_fhir_tool (if you have a FHIR
   DiagnosticReport ID or ServiceRequest ID) or fetch_report_dicom_tool (if you
   only have a DICOM accession number or patient ID).
2. If you used fetch_report_dicom_tool, the returned study will have no
   `report_text` and no `acr_category` — DICOM worklists carry scheduling
   data, not findings. In that case, call fetch_radiologist_findings_tool
   with the same accession_number to retrieve the radiologist's signed
   report from the report broker. That tool also returns the
   service_request_id and patient_id needed for the rest of the workflow.
   If fetch_radiologist_findings_tool returns found=false, the report has
   not been signed yet — stop and report that no findings are available.
3. Read the returned study or findings object's `acr_category`. If it is
   "Cat3" or null after both fetches, stop and report that no critical
   communication is needed. (The classifier will have populated acr_category
   from the report text when the FHIR tag was missing.)
4. For Cat1 or Cat2: call resolve_provider_tool with the service_request_id to
   find the ordering physician's contact details.
5. Call dispatch_communication_tool to record the notification in FHIR. Pass
   the service_request_id, patient_id, the practitioner ID returned by
   resolve_provider, the ACR category, and a one-sentence finding_summary
   pulled from the study's impression.
6. Call track_acknowledgment_tool with action="create" to start the ack
   countdown. Use 60 minutes for Cat1, 1440 minutes (24 hours) for Cat2.
7. If asked to check on a Task, call track_acknowledgment_tool with
   action="check". If the Task is overdue, call escalate_tool — pass the
   original_task_id and the same study details. This will notify the on-call
   provider and create a new Task.
8. At any point, call query_audit_tool to return the full Communication and
   Task history for a service_request_id or patient_id.

Be precise about which IDs you are working with. Always confirm the result of
each tool call in your response. If a tool returns an error, surface it
clearly and do not proceed.
"""


def _wrap_model(model_name: str):
    """Choose the right ADK model wrapper.

    Native Gemini models (``gemini-*``) pass through as plain strings — the
    ADK default code path handles tool-call routing for them. Anything else
    (``gemma-*``, ``claude-*``, OpenAI, etc.) is routed through ADK's
    LiteLlm wrapper, which translates between provider-specific function-call
    formats and the ADK tool-call protocol. This is what lets Gemma 4 emit a
    structured function call that actually executes the underlying Python tool.
    """
    if model_name.startswith("gemini-"):
        return model_name
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as e:
        raise RuntimeError(
            f"CRITCOM_LLM_MODEL={model_name!r} requires the litellm extras. "
            "Install with: pip install 'google-adk[lite_llm]' litellm"
        ) from e
    # LiteLLM routes Google AI Studio models through the gemini/ namespace
    # regardless of whether the underlying model is Gemini or Gemma.
    if model_name.startswith(("gemma-", "gemma/")):
        litellm_id = f"gemini/{model_name.removeprefix('gemma/').removeprefix('models/')}"
    elif "/" in model_name:
        litellm_id = model_name  # user passed an already-qualified id like "anthropic/claude-..."
    else:
        litellm_id = model_name
    return LiteLlm(model=litellm_id)


def build_agent():
    """Build the ADK Agent. Imported lazily so the module can be loaded
    without google-adk installed (useful for unit tests)."""
    try:
        from google.adk.agents import Agent
    except ImportError as e:
        raise RuntimeError(
            "google-adk is required to build the agent. Install with: pip install google-adk"
        ) from e

    model = _wrap_model(os.getenv("CRITCOM_LLM_MODEL", "gemini-2.0-flash"))
    extension_uri = os.getenv(
        "CRITCOM_FHIR_EXTENSION_URI",
        "https://github.com/iupui-soic/agentic-ai-radiology/schemas/a2a/v1/fhir-context",
    )

    agent = Agent(
        name="critcom",
        model=model,
        description="Critical results communication agent for radiology",
        instruction=INSTRUCTION,
        tools=ALL_TOOLS,
        before_model_callback=make_extract_fhir_context(extension_uri),
    )
    return agent


# Module-level instance — only built when ADK is available
try:
    root_agent = build_agent()
except Exception:  # noqa: BLE001
    root_agent = None  # type: ignore[assignment]
