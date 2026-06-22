"""Direct FHIR state verification for the eval harness.

Scorer 3 (state validity) is only meaningful if it confirms the side effects
actually landed on HAPI. The narrative-parsing fallback in `scorers.score_state`
reads the agent's prose, which can claim success without it being true.

This module queries HAPI directly over httpx (the only dep the eval image
ships) to confirm a Communication + acknowledgment Task exist for a case. It
mirrors the search pattern in `critcom.fhir.client.search_audit`
(Communication?based-on=..., then Task?focus=Communication/<id>) without
importing the critcom package, which is not installed in the eval image.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

_FHIR_HEADERS = {"Accept": "application/fhir+json"}


@dataclass
class FhirState:
    communication_present: bool
    task_present: bool
    task_deadline_minutes: int | None
    reachable: bool  # False if the FHIR server could not be queried


def _entries(bundle: dict) -> list[dict]:
    return [e.get("resource") or {} for e in (bundle.get("entry") or [])]


def _deadline_minutes(task: dict) -> int | None:
    """Minutes between the Task's ack window start and end, if both present."""
    period = ((task.get("restriction") or {}).get("period")) or {}
    start, end = period.get("start"), period.get("end")
    if not (start and end):
        return None
    try:
        dt_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        dt_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((dt_end - dt_start).total_seconds() / 60)


def check_state(fhir_base_url: str, service_request_id: str, timeout: float = 10.0) -> FhirState:
    """Return whether a Communication + Task exist on HAPI for the ServiceRequest.

    On any transport/parse error returns reachable=False so the caller can fall
    back to narrative parsing rather than scoring a false failure.
    """
    base = fhir_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout, headers=_FHIR_HEADERS) as client:
            comm_resp = client.get(
                f"{base}/Communication",
                params={"based-on": f"ServiceRequest/{service_request_id}", "_sort": "-sent"},
            )
            comm_resp.raise_for_status()
            comms = _entries(comm_resp.json())

            tasks: list[dict] = []
            for comm in comms:
                comm_id = comm.get("id")
                if not comm_id:
                    continue
                task_resp = client.get(
                    f"{base}/Task", params={"focus": f"Communication/{comm_id}"}
                )
                task_resp.raise_for_status()
                tasks.extend(_entries(task_resp.json()))
    except (httpx.HTTPError, ValueError, KeyError):
        return FhirState(False, False, None, reachable=False)

    deadline = next((m for t in tasks if (m := _deadline_minutes(t)) is not None), None)
    return FhirState(
        communication_present=bool(comms),
        task_present=bool(tasks),
        task_deadline_minutes=deadline,
        reachable=True,
    )