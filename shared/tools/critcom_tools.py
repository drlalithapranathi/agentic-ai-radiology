"""
ADK-compatible tool wrappers.

Each function below has the signature expected by Google ADK
(last argument is `tool_context`). Each delegates to the corresponding
critcom.tools.<tool>.run() coroutine after pulling FHIR credentials from
session state into environment variables that the underlying FHIR client
reads.

This is the only "glue layer" between the platform-agnostic critcom.tools.*
implementations and the ADK runtime.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _apply_fhir_context(tool_context: Any) -> None:
    """Bind this request's fhir_url/fhir_token (from session state) for the client.

    Uses task-local contextvars rather than os.environ so concurrent requests
    don't clobber each other and a request without FHIR context can't inherit a
    previous caller's token. Always sets — passing None clears any prior value.
    """
    from critcom.fhir.context import set_fhir_context

    state = getattr(tool_context, "state", {}) or {}
    set_fhir_context(fhir_url=state.get("fhir_url"), fhir_token=state.get("fhir_token"))


async def fetch_report_fhir_tool(
    diagnostic_report_id: str | None = None,
    service_request_id: str | None = None,
    tool_context: Any = None,
) -> dict:
    """Retrieve a signed DiagnosticReport from FHIR, normalize to a study object."""
    from critcom.tools.fetch_report_fhir import run
    if tool_context is not None:
        _apply_fhir_context(tool_context)
    return await run({
        "diagnostic_report_id": diagnostic_report_id,
        "service_request_id": service_request_id,
    })


async def fetch_report_dicom_tool(
    accession_number: str | None = None,
    patient_id: str | None = None,
    tool_context: Any = None,
) -> dict:
    """Query a DICOM Modality Worklist via C-FIND. Fallback path when no FHIR DiagnosticReport exists."""
    from critcom.tools.fetch_report_dicom import run
    return await run({
        "accession_number": accession_number,
        "patient_id": patient_id,
    })


async def fetch_radiologist_findings_tool(
    accession_number: str,
    tool_context: Any = None,
) -> dict:
    """Retrieve radiologist-signed findings text for a DICOM accession from the local report broker."""
    from critcom.tools.fetch_radiologist_findings import run
    return await run({"accession_number": accession_number})


async def resolve_provider_tool(
    service_request_id: str,
    on_call: bool = False,
    tool_context: Any = None,
) -> dict:
    """Walk a FHIR ServiceRequest to find the ordering provider's contact details."""
    from critcom.tools.resolve_provider import run
    if tool_context is not None:
        _apply_fhir_context(tool_context)
    return await run({"service_request_id": service_request_id, "on_call": on_call})


async def dispatch_communication_tool(
    service_request_id: str,
    patient_id: str,
    recipient_practitioner_id: str,
    acr_category: str,
    finding_summary: str,
    tool_context: Any = None,
) -> dict:
    """Create a FHIR Communication resource recording that a notification was dispatched."""
    from critcom.tools.dispatch_communication import run
    if tool_context is not None:
        _apply_fhir_context(tool_context)
    return await run({
        "service_request_id": service_request_id,
        "patient_id": patient_id,
        "recipient_practitioner_id": recipient_practitioner_id,
        "acr_category": acr_category,
        "finding_summary": finding_summary,
    })


async def track_acknowledgment_tool(
    action: str,
    communication_id: str | None = None,
    practitioner_id: str | None = None,
    patient_id: str | None = None,
    timeout_minutes: int | None = None,
    task_id: str | None = None,
    tool_context: Any = None,
) -> dict:
    """Create / check / mark a FHIR Task that tracks provider acknowledgment."""
    from critcom.tools.track_acknowledgment import run
    if tool_context is not None:
        _apply_fhir_context(tool_context)
    args: dict[str, Any] = {"action": action}
    if communication_id is not None:
        args["communication_id"] = communication_id
    if practitioner_id is not None:
        args["practitioner_id"] = practitioner_id
    if patient_id is not None:
        args["patient_id"] = patient_id
    if timeout_minutes is not None:
        args["timeout_minutes"] = timeout_minutes
    if task_id is not None:
        args["task_id"] = task_id
    return await run(args)


async def escalate_tool(
    original_task_id: str,
    service_request_id: str,
    patient_id: str,
    acr_category: str,
    finding_summary: str,
    timeout_minutes: int,
    tool_context: Any = None,
) -> dict:
    """Mark the overdue Task failed and notify the on-call backup with a new Task."""
    from critcom.tools.escalate import run
    if tool_context is not None:
        _apply_fhir_context(tool_context)
    return await run({
        "original_task_id": original_task_id,
        "service_request_id": service_request_id,
        "patient_id": patient_id,
        "acr_category": acr_category,
        "finding_summary": finding_summary,
        "timeout_minutes": timeout_minutes,
    })


async def query_audit_tool(
    service_request_id: str | None = None,
    patient_id: str | None = None,
    tool_context: Any = None,
) -> dict:
    """Return all Communications and Tasks for a case — full audit trail."""
    from critcom.tools.query_audit import run
    if tool_context is not None:
        _apply_fhir_context(tool_context)
    return await run({
        "service_request_id": service_request_id,
        "patient_id": patient_id,
    })


ALL_TOOLS = [
    fetch_report_fhir_tool,
    fetch_report_dicom_tool,
    fetch_radiologist_findings_tool,
    resolve_provider_tool,
    dispatch_communication_tool,
    track_acknowledgment_tool,
    escalate_tool,
    query_audit_tool,
]
