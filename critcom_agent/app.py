"""
A2A app entry point for CritCom.

Run with:
    uvicorn critcom_agent.app:a2a_app --host 0.0.0.0 --port 8001

Or via ADK web UI for local development (no API key needed):
    adk web .
"""

from __future__ import annotations

import os

from shared.app_factory import create_a2a_app
from shared.logging_utils import configure_logging

configure_logging("critcom")

from critcom_agent.agent import root_agent  # noqa: E402

CRITCOM_AGENT_URL = os.getenv("CRITCOM_AGENT_URL", "http://localhost:8001")
CRITCOM_FHIR_EXTENSION_URI = os.getenv(
    "CRITCOM_FHIR_EXTENSION_URI",
    "https://github.com/iupui-soic/agentic-ai-radiology/schemas/a2a/v1/fhir-context",
)
REQUIRE_API_KEY = os.getenv("CRITCOM_REQUIRE_API_KEY", "true").lower() == "true"

SKILLS = [
    {
        "id": "process_critical_finding",
        "name": "Process critical radiology finding",
        "description": (
            "Given a signed DiagnosticReport or DICOM accession, classifies the "
            "ACR criticality (Cat1/Cat2/Cat3), resolves the ordering provider, "
            "dispatches a notification, opens a FHIR Task, and escalates to "
            "on-call if the acknowledgment window expires."
        ),
        "tags": ["radiology", "critical-results", "fhir", "acr", "communication"],
        "examples": [
            "Process DiagnosticReport dr-001 and notify the ordering physician.",
            "Check the acknowledgment status of Task task-abc and escalate if overdue.",
            "Show the audit history for ServiceRequest sr-002.",
        ],
    },
]

a2a_app = create_a2a_app(
    agent=root_agent,
    name="CritCom",
    description=(
        "Critical results communication agent for radiology. Routes signed "
        "DiagnosticReports (or DICOM worklist entries) to the right ordering "
        "physician, tracks acknowledgment, and escalates to on-call coverage "
        "if no response within the ACR-defined timeframe."
    ),
    url=CRITCOM_AGENT_URL,
    version="0.1.0",
    fhir_extension_uri=CRITCOM_FHIR_EXTENSION_URI,
    require_api_key=REQUIRE_API_KEY,
    skills=SKILLS,
)
