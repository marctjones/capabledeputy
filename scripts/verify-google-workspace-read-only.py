#!/usr/bin/env python3
"""Verify live Google Workspace read-only acceptance without exposing content.

This is intentionally opt-in: set ``CAPDEP_GOOGLE_WORKSPACE_LIVE=1`` only
after the daemon-owned ``gws`` credential store has the exact Gmail, Calendar,
and Drive read scopes.  The verifier performs three bounded reads through the
daemon's ``tool.call`` chokepoint, but reports only decisions and structural
evidence.  It never prints provider results, account identity, tokens, or
audit payload contents.

The test also proves that a session without the read capabilities cannot
dispatch any of the reads.  No mutations, sharing, sends, or scope changes are
performed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from typing import Any

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

_LIVE_ENV = "CAPDEP_GOOGLE_WORKSPACE_LIVE"
_READ_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    }
)
_READ_CASES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "Gmail",
        "google-workspace.gmail_messages_list",
        {"userId": "me", "maxResults": 1},
    ),
    (
        "Calendar",
        "google-workspace.calendar_events_list",
        {
            "calendarId": "primary",
            "maxResults": 1,
            "singleEvents": True,
            "orderBy": "startTime",
        },
    ),
    (
        "Drive",
        "google-workspace.drive_files_list",
        {"pageSize": 1, "fields": "files(id,name,mimeType)"},
    ),
)


async def _call(client: DaemonClient, method: str, params: dict[str, Any]) -> Any:
    return await asyncio.wait_for(client.call(method, params), timeout=20)


def _google_connector(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    connectors = payload.get("connectors")
    if not isinstance(connectors, list):
        raise RuntimeError("connector.status did not return a connector list")
    connector = next(
        (
            item
            for item in connectors
            if isinstance(item, Mapping) and item.get("id") == "google-workspace"
        ),
        None,
    )
    if connector is None:
        raise RuntimeError("connector.status did not return Google Workspace")
    return connector


def _assert_connector_ready(connector: Mapping[str, Any]) -> None:
    if connector.get("status") != "ready_read_only":
        raise RuntimeError(
            "Google Workspace is not ready_read_only; run the explicit least-privilege "
            "OAuth setup before live verification."
        )
    present = frozenset(str(scope) for scope in connector.get("read_scopes_present", ()))
    missing = connector.get("missing_read_scopes")
    writes = connector.get("write_scopes_present")
    if present != _READ_SCOPES or missing or writes:
        raise RuntimeError("Google Workspace connector posture is not exact read-only")
    forbidden_keys = {"token", "access_token", "refresh_token", "account", "email", "path"}
    if forbidden_keys & set(connector):
        raise RuntimeError("connector.status exposed credential or account material")


async def _assert_denied_without_capability(client: DaemonClient) -> None:
    session = await _call(
        client,
        "session.new",
        {"intent": "Google Workspace read denial verification", "owner": "google-live-verify"},
    )
    session_id = str(session.get("id") or "")
    if not session_id:
        raise RuntimeError("denial session did not have an id")
    for name, tool, args in _READ_CASES:
        result = await _call(
            client,
            "tool.test",
            {"tool": tool, "session_id": session_id, "args": args},
        )
        if result.get("decision") != "deny":
            raise RuntimeError(f"{name} read was not denied without its capability")


async def _assert_live_reads_and_evidence(client: DaemonClient) -> None:
    # This matches the foreground session creation path used by CapDepMac.
    session = await _call(
        client,
        "session.new",
        {
            "intent": "Google Workspace read-only acceptance verification",
            "owner": "CapDepMac",
            "purpose_handle": "general",
        },
    )
    session_id = str(session.get("id") or "")
    if not session_id:
        raise RuntimeError("foreground session did not have an id")

    for name, tool, args in _READ_CASES:
        result = await _call(
            client,
            "tool.call",
            {"tool": tool, "session_id": session_id, "args": args},
        )
        if result.get("decision") != "allow" or result.get("error") is not None:
            raise RuntimeError(f"{name} read did not complete through the policy chokepoint")
        if result.get("output") is None:
            raise RuntimeError(f"{name} read returned no in-session result")
        print(f"OK  {name} read: allowed; result retained only in the labeled session flow")

    context = await _call(client, "session.security_context", {"session_id": session_id})
    labels = context.get("labels", {}).get("legacy_label_set", [])
    required_labels = {"confidential.personal", "untrusted.external"}
    if not required_labels <= set(labels):
        raise RuntimeError("Google results did not propagate personal and external labels")
    provenance = context.get("provenance", {})
    if len(provenance.get("nodes", [])) < len(_READ_CASES) or not provenance.get("edges"):
        raise RuntimeError("Google results did not produce provenance evidence")

    audit = await _call(client, "audit.list", {"session_id": session_id, "limit": 100})
    events = audit.get("events", [])
    returned = [event for event in events if event.get("event_type") == "tool.returned"]
    if len(returned) != len(_READ_CASES):
        raise RuntimeError("expected one tool.returned event per Google read")
    for event in returned:
        payload = event.get("payload", {})
        if (
            payload.get("output_redacted") is not True
            or payload.get("output_type") != "dict"
            or len(str(payload.get("output_digest") or "")) != 64
            or not isinstance(payload.get("output_size_bytes"), int)
            or payload.get("output_size_bytes") <= 0
            or "output" in payload
        ):
            raise RuntimeError("tool.returned audit evidence copied or omitted result metadata")
    print("OK  labels, provenance, and redacted audit commitments verified")


async def main() -> int:
    if os.environ.get(_LIVE_ENV) != "1":
        print(f"SKIP set {_LIVE_ENV}=1 to run credential-gated Google Workspace acceptance")
        return 0

    client = DaemonClient(default_socket_path())
    connector = _google_connector(await _call(client, "connector.status", {}))
    _assert_connector_ready(connector)
    print("OK  daemon-owned Google Workspace credentials are exact read-only")
    await _assert_denied_without_capability(client)
    print("OK  all Google reads denied when their session capabilities are absent")
    await _assert_live_reads_and_evidence(client)
    print("Google Workspace read-only acceptance passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as error:
        print(f"FAIL {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
