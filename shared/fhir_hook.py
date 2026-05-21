"""
extract_fhir_context — ADK before_model_callback that pulls FHIR credentials
from the A2A message metadata and stores them in session state.

Tools then read fhir_url, fhir_token, patient_id from tool_context.state.

Mirrors po-adk-python/shared/fhir_hook.py.
"""

from __future__ import annotations

import hashlib
import logging
import os

logger = logging.getLogger(__name__)

# The extension URI in A2A message metadata that carries FHIR context.
# Override via CRITCOM_FHIR_EXTENSION_URI for caller-specific deployments.
FHIR_EXTENSION_URI = os.getenv(
    "CRITCOM_FHIR_EXTENSION_URI",
    "https://github.com/iupui-soic/agentic-ai-radiology/schemas/a2a/v1/fhir-context",
)


def _fingerprint(token: str) -> str:
    return f"len={len(token)} sha256={hashlib.sha256(token.encode()).hexdigest()[:8]}"


def make_extract_fhir_context(extension_uri: str = FHIR_EXTENSION_URI):
    """Factory so each agent can declare its own extension URI."""

    async def extract_fhir_context(callback_context, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Runs before each LLM call. Reads metadata, writes to session state."""
        try:
            invocation = callback_context._invocation_context  # type: ignore[attr-defined]
            session = invocation.session
            metadata = getattr(invocation, "user_content_metadata", None) or {}
            if not metadata:
                # Try other common paths
                req = getattr(invocation, "request", None)
                if req is not None:
                    params = getattr(req, "params", None) or {}
                    metadata = params.get("metadata") if isinstance(params, dict) else {}
        except Exception as e:
            logger.debug("hook_callback_context_unavailable error=%s", e)
            return None

        if not metadata:
            logger.info("hook_called_no_metadata")
            return None

        fhir_ctx = metadata.get(extension_uri)
        if fhir_ctx is None:
            logger.info("hook_called_fhir_not_found uri=%s", extension_uri)
            return None
        if not isinstance(fhir_ctx, dict):
            logger.warning("hook_called_fhir_malformed type=%s", type(fhir_ctx).__name__)
            return None

        fhir_url = fhir_ctx.get("fhirUrl")
        fhir_token = fhir_ctx.get("fhirToken")
        patient_id = fhir_ctx.get("patientId")

        if fhir_url:
            logger.info("FHIR_URL_FOUND")
            session.state["fhir_url"] = fhir_url
        if fhir_token:
            logger.info("FHIR_TOKEN_FOUND %s", _fingerprint(fhir_token))
            session.state["fhir_token"] = fhir_token
        if patient_id:
            logger.info("FHIR_PATIENT_FOUND")
            session.state["patient_id"] = patient_id

        if fhir_url and fhir_token and patient_id:
            logger.info("hook_called_fhir_found")
        return None

    return extract_fhir_context


# Default callback for direct import
extract_fhir_context = make_extract_fhir_context()
