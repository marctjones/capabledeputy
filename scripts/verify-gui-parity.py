#!/usr/bin/env python3
"""Verify daemon RPCs used by the macOS Swift GUI are reachable.

Exits 0 when parity checks pass. Intended to run before opening CapDepMac.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from typing import Any

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

GUI_METHODS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("ping", {}),
    ("app.status", {}),
    ("setup.plan", {}),
    ("workflow.templates", {}),
    ("session.list", {}),
    ("approval.list", {"status": "pending"}),
    ("audit.tail", {"limit": 5}),
    ("settings.get", {}),
    ("config.validate", {}),
    ("config.log_locations", {}),
    ("connector.status", {}),
    ("runtime.status", {}),
    ("source_binding.list", {}),
    ("provenance.graph", {}),
    ("relationship_group.list", {}),
    ("approval_pattern.list", {}),
    ("memory.entries", {}),
    ("tool.list", {}),
    ("override.list", {}),
    ("client.registry.list", {"kind": "onguard"}),
    ("client.queue.list", {}),
    ("schedule.list", {}),
    ("artifact.list", {}),
    ("client.events.list", {"limit": 10}),
    ("client.config.list", {}),
    ("setup.google.oauth_status", {}),
    ("macos.frontmost_context", {}),
)


async def _call(client: DaemonClient, method: str, params: dict[str, Any]) -> Any:
    return await asyncio.wait_for(client.call(method, params), timeout=10)


async def _check_chat_session(client: DaemonClient) -> None:
    created = await _call(
        client,
        "session.new",
        {
            "intent": "gui parity chat probe",
            "owner": "verify-gui-parity",
            "purpose_handle": "general",
        },
    )
    session_id = str(created.get("id") or "")
    if not session_id:
        raise RuntimeError("session.new did not return an id")

    started = await _call(
        client,
        "session.turn.start",
        {
            "session_id": session_id,
            "message": "Reply with exactly: parity-ok",
            "client_id": "verify-gui-parity",
        },
    )
    turn = started.get("turn") or {}
    turn_id = str(turn.get("id") or "")
    if not turn_id:
        raise RuntimeError("session.turn.start did not return a turn id")

    try:
        for _ in range(400):
            observed = await _call(client, "session.turn.get", {"turn_id": turn_id})
            status = str((observed.get("turn") or {}).get("status") or "")
            if status in {"completed", "interrupted", "error"}:
                if status != "completed":
                    err = (observed.get("turn") or {}).get("error") or "unknown turn error"
                    raise RuntimeError(f"chat turn {status}: {err}")
                result = (observed.get("turn") or {}).get("result") or {}
                if str(result.get("content") or "").strip() != "parity-ok":
                    raise RuntimeError("chat completed without the requested parity-ok response")
                return
            await asyncio.sleep(0.25)
        raise RuntimeError("chat turn timed out waiting for completion")
    except BaseException:
        with suppress(Exception):
            await _call(client, "session.turn.cancel", {"turn_id": turn_id})
        raise


async def main() -> int:
    client = DaemonClient(default_socket_path())
    failures: list[str] = []

    for method, params in GUI_METHODS:
        try:
            await _call(client, method, params)
            print(f"OK  {method}")
        except Exception as e:
            failures.append(f"{method}: {e}")
            print(f"FAIL {method}: {e}", file=sys.stderr)

    try:
        await asyncio.wait_for(_check_chat_session(client), timeout=120)
        print("OK  chat session (session.new + session.turn.start)")
    except Exception as e:
        failures.append(f"chat: {e}")
        print(f"FAIL chat session: {e}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} parity check(s) failed.", file=sys.stderr)
        return 1
    print("\nGUI daemon parity OK (including chat session).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
