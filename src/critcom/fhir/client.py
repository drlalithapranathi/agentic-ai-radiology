"""
Async FHIR R4 client backed by httpx + tenacity retries.

Usage
-----
async with FHIRClient.from_env() as client:
    sr = await client.get_service_request("sr-001")
    practitioner = await client.get_practitioner("prac-001")
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from critcom.fhir.context import get_fhir_token, get_fhir_url
from critcom.fhir.models import (
    Bundle,
    BundleEntry,
    Communication,
    DiagnosticReport,
    Patient,
    Practitioner,
    PractitionerRole,
    ServiceRequest,
    Task,
    TaskStatus,
)

log = structlog.get_logger(__name__)

_RETRYABLE = (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)


def _retry_decorator():
    return retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(int(os.getenv("CRITCOM_FHIR_MAX_RETRIES", "3"))),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )


class FHIRError(Exception):
    """Raised when the FHIR server returns a non-2xx response."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"FHIR {status_code}: {body[:200]}")


class FHIRClient:
    """Thin async wrapper around the HAPI FHIR R4 REST API."""

    def __init__(self, base_url: str, bearer_token: str | None = None, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        headers: dict[str, str] = {"Accept": "application/fhir+json", "Content-Type": "application/fhir+json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        self._client = httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=timeout)

    @classmethod
    def from_env(cls) -> "FHIRClient":
        """Build a client from the per-request FHIR context, falling back to env.

        The caller-supplied URL/token (set per task via fhir.context) take
        precedence; the env vars are the deployment default when no per-request
        context is present.
        """
        return cls(
            base_url=get_fhir_url() or os.getenv("CRITCOM_FHIR_BASE_URL", "http://localhost:8080/fhir"),
            bearer_token=get_fhir_token() or os.getenv("CRITCOM_FHIR_BEARER_TOKEN") or None,
            timeout=float(os.getenv("CRITCOM_FHIR_TIMEOUT_SECONDS", "10")),
        )

    async def __aenter__(self) -> "FHIRClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    @_retry_decorator()
    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        log.debug("fhir.get", path=path, params=params)
        r = await self._client.get(path, params=params)
        if r.status_code >= 400:
            raise FHIRError(r.status_code, r.text)
        return r.json()  # type: ignore[no-any-return]

    @_retry_decorator()
    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        log.debug("fhir.post", path=path)
        r = await self._client.post(path, json=body)
        if r.status_code >= 400:
            raise FHIRError(r.status_code, r.text)
        return r.json()  # type: ignore[no-any-return]

    @_retry_decorator()
    async def _put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        log.debug("fhir.put", path=path)
        r = await self._client.put(path, json=body)
        if r.status_code >= 400:
            raise FHIRError(r.status_code, r.text)
        return r.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # ServiceRequest
    # ------------------------------------------------------------------

    async def get_service_request(self, resource_id: str) -> ServiceRequest:
        data = await self._get(f"/ServiceRequest/{resource_id}")
        return ServiceRequest.model_validate(data)

    async def search_service_requests(self, patient_id: str) -> list[ServiceRequest]:
        data = await self._get("/ServiceRequest", params={"subject": f"Patient/{patient_id}", "_sort": "-_lastUpdated"})
        bundle = Bundle.model_validate(data)
        return [ServiceRequest.model_validate(e.resource) for e in bundle.entry if e.resource]

    # ------------------------------------------------------------------
    # DiagnosticReport
    # ------------------------------------------------------------------

    async def get_diagnostic_report(self, resource_id: str) -> DiagnosticReport:
        data = await self._get(f"/DiagnosticReport/{resource_id}")
        return DiagnosticReport.model_validate(data)

    async def search_diagnostic_reports(
        self,
        status: str = "final",
        based_on: str | None = None,
        patient_id: str | None = None,
        limit: int = 50,
    ) -> list[DiagnosticReport]:
        params: dict[str, str] = {"status": status, "_count": str(limit), "_sort": "-_lastUpdated"}
        if based_on:
            params["based-on"] = f"ServiceRequest/{based_on}"
        if patient_id:
            params["subject"] = f"Patient/{patient_id}"
        data = await self._get("/DiagnosticReport", params=params)
        bundle = Bundle.model_validate(data)
        return [DiagnosticReport.model_validate(e.resource) for e in bundle.entry if e.resource]

    # ------------------------------------------------------------------
    # Practitioner / PractitionerRole
    # ------------------------------------------------------------------

    async def get_practitioner(self, resource_id: str) -> Practitioner:
        data = await self._get(f"/Practitioner/{resource_id}")
        return Practitioner.model_validate(data)

    async def get_practitioner_role(self, resource_id: str) -> PractitionerRole:
        data = await self._get(f"/PractitionerRole/{resource_id}")
        return PractitionerRole.model_validate(data)

    async def search_practitioner_roles(self, practitioner_id: str) -> list[PractitionerRole]:
        data = await self._get(
            "/PractitionerRole",
            params={"practitioner": f"Practitioner/{practitioner_id}", "active": "true"},
        )
        bundle = Bundle.model_validate(data)
        return [PractitionerRole.model_validate(e.resource) for e in bundle.entry if e.resource]

    async def search_on_call_roles(self, specialty_code: str | None = None) -> list[PractitionerRole]:
        """Return active PractitionerRoles tagged as on-call."""
        params: dict[str, str] = {"active": "true"}
        if specialty_code:
            params["specialty"] = specialty_code
        data = await self._get("/PractitionerRole", params=params)
        bundle = Bundle.model_validate(data)
        roles = [PractitionerRole.model_validate(e.resource) for e in bundle.entry if e.resource]
        # Filter to roles whose code includes "on-call"
        return [
            r for r in roles
            if any(
                any(c.code == "on-call" for c in cc.coding)
                for cc in r.code
            )
        ]

    # ------------------------------------------------------------------
    # Patient
    # ------------------------------------------------------------------

    async def get_patient(self, resource_id: str) -> Patient:
        data = await self._get(f"/Patient/{resource_id}")
        return Patient.model_validate(data)

    # ------------------------------------------------------------------
    # Communication
    # ------------------------------------------------------------------

    async def create_communication(self, comm: Communication) -> Communication:
        data = await self._post("/Communication", comm.model_dump(mode="json", exclude_none=True, by_alias=True))
        return Communication.model_validate(data)

    async def get_communication(self, resource_id: str) -> Communication:
        data = await self._get(f"/Communication/{resource_id}")
        return Communication.model_validate(data)

    async def search_communications(self, service_request_id: str) -> list[Communication]:
        data = await self._get(
            "/Communication",
            params={"based-on": f"ServiceRequest/{service_request_id}", "_sort": "-sent"},
        )
        bundle = Bundle.model_validate(data)
        return [Communication.model_validate(e.resource) for e in bundle.entry if e.resource]

    async def search_communications_by_patient(self, patient_id: str) -> list[Communication]:
        data = await self._get(
            "/Communication",
            params={"subject": f"Patient/{patient_id}", "_sort": "-sent"},
        )
        bundle = Bundle.model_validate(data)
        return [Communication.model_validate(e.resource) for e in bundle.entry if e.resource]

    # ------------------------------------------------------------------
    # Task (acknowledgment)
    # ------------------------------------------------------------------

    async def create_task(self, task: Task) -> Task:
        data = await self._post("/Task", task.model_dump(mode="json", exclude_none=True, by_alias=True))
        return Task.model_validate(data)

    async def get_task(self, resource_id: str) -> Task:
        data = await self._get(f"/Task/{resource_id}")
        return Task.model_validate(data)

    async def update_task_status(self, resource_id: str, status: TaskStatus) -> Task:
        task = await self.get_task(resource_id)
        task.status = status
        data = await self._put(f"/Task/{resource_id}", task.model_dump(mode="json", exclude_none=True, by_alias=True))
        return Task.model_validate(data)

    async def search_tasks_for_communication(self, communication_id: str) -> list[Task]:
        data = await self._get(
            "/Task",
            params={"focus": f"Communication/{communication_id}", "_sort": "-_lastUpdated"},
        )
        bundle = Bundle.model_validate(data)
        return [Task.model_validate(e.resource) for e in bundle.entry if e.resource]

    # ------------------------------------------------------------------
    # Bulk seed helper (used by scripts/seed.py)
    # ------------------------------------------------------------------

    async def transaction_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """POST a FHIR transaction bundle."""
        return await self._post("/", bundle)

    async def upsert_resource(self, resource_type: str, resource_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """PUT a resource by id (create-or-update)."""
        return await self._put(f"/{resource_type}/{resource_id}", body)

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------

    async def search_audit(
        self,
        service_request_id: str | None = None,
        patient_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, list[Any]]:
        """Return communications + tasks related to a case for audit display.

        Looks up Communications by ServiceRequest when given (the precise key),
        otherwise by Patient. Tasks are then collected via each Communication's
        focus reference.
        """
        result: dict[str, list[Any]] = {"communications": [], "tasks": []}

        if service_request_id:
            comms = await self.search_communications(service_request_id)
        elif patient_id:
            comms = await self.search_communications_by_patient(patient_id)
        else:
            return result

        result["communications"] = [c.model_dump(mode="json") for c in comms]
        for c in comms:
            if c.id:
                tasks = await self.search_tasks_for_communication(c.id)
                result["tasks"].extend(t.model_dump(mode="json") for t in tasks)

        return result

    # ------------------------------------------------------------------
    # Bundle helpers for entries
    # ------------------------------------------------------------------

    @staticmethod
    def extract_resources(bundle_data: dict[str, Any]) -> list[dict[str, Any]]:
        bundle = Bundle.model_validate(bundle_data)
        return [e.resource for e in bundle.entry if e.resource]
