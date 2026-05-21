"""Thin A2A JSON-RPC client for the CritCom eval harness.

Wraps the message/send call against either the live VM or a local docker
instance, returns the parsed final-message text plus raw response."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class AgentReply:
    success: bool
    text: str
    raw: dict[str, Any]
    elapsed_seconds: float
    error: str | None = None


def send(base_url: str, prompt: str, timeout: float = 120.0) -> AgentReply:
    """Send one message to the A2A agent and return its final reply."""
    url = base_url.rstrip("/") + "/"
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "text", "text": prompt}],
            }
        },
    }
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers={"Content-Type": "application/json"})
        elapsed = time.perf_counter() - started
        if resp.status_code != 200:
            return AgentReply(
                success=False,
                text="",
                raw={"status_code": resp.status_code, "body": resp.text[:500]},
                elapsed_seconds=elapsed,
                error=f"HTTP {resp.status_code}",
            )
        body = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        return AgentReply(
            success=False,
            text="",
            raw={},
            elapsed_seconds=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )

    text = _extract_text(body)
    return AgentReply(success=True, text=text, raw=body, elapsed_seconds=elapsed)


def _extract_text(body: dict[str, Any]) -> str:
    """Pull the final agent reply text from an A2A response envelope."""
    result = body.get("result") or {}

    # 1. Single Message result (common path)
    parts = result.get("parts") or []
    if parts:
        return "\n".join(p.get("text", "") for p in parts if p.get("kind") == "text").strip()

    # 2. Task result with status.message.parts
    status_msg = (result.get("status") or {}).get("message") or {}
    msg_parts = status_msg.get("parts") or []
    if msg_parts:
        return "\n".join(p.get("text", "") for p in msg_parts if p.get("kind") == "text").strip()

    # 3. Task result with history — pick the last agent turn
    history = result.get("history") or []
    for entry in reversed(history):
        if entry.get("role") in {"agent", "assistant"}:
            for part in entry.get("parts") or []:
                if part.get("kind") == "text" and part.get("text"):
                    return part["text"].strip()

    # 4. Artifacts (some A2A servers route final text here)
    artifacts = result.get("artifacts") or []
    for art in artifacts:
        for part in art.get("parts") or []:
            if part.get("kind") == "text" and part.get("text"):
                return part["text"].strip()

    return ""
