"""Per-request FHIR credentials, isolated with contextvars.

The ADK tool wrappers receive a caller's FHIR URL/token in the A2A request and
need to hand them to the FHIRClient. Stashing them in ``os.environ`` (the old
approach) is process-global: a request that carries no FHIR context would read
the *previous* request's leftover token, and concurrent requests can read each
other's values. ContextVars are bound to the current async task, so each
request sees only its own credentials (or None, falling back to the configured
default), with no cross-request bleed.
"""

from __future__ import annotations

import contextvars

_fhir_url: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "critcom_fhir_url", default=None
)
_fhir_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "critcom_fhir_token", default=None
)


def set_fhir_context(*, fhir_url: str | None, fhir_token: str | None) -> None:
    """Bind (or clear) the FHIR credentials for the current task.

    Always call this per tool invocation — passing None clears any value
    inherited from an earlier call in the same context.
    """
    _fhir_url.set(fhir_url)
    _fhir_token.set(fhir_token)


def get_fhir_url() -> str | None:
    return _fhir_url.get()


def get_fhir_token() -> str | None:
    return _fhir_token.get()