#!/usr/bin/env python3
"""Real-daemon, no-effect daily-driver policy acceptance probe (#451).

This intentionally uses ``tool.call`` only for an inert missing memory key and
for email attempts that are stopped by the daemon before delivery.  It records
the daemon's own policy/audit projection, rather than treating a client-side
expectation as proof of authorization.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

RECIPIENT = "recipient@acceptance-test.invalid"


async def _call(client: DaemonClient, method: str, params: dict[str, Any]) -> dict[str, Any]:
    result = await asyncio.wait_for(client.call(method, params), timeout=20)
    if not isinstance(result, dict):
        raise RuntimeError(f"{method} returned a non-object result")
    return result


async def _session(client: DaemonClient, name: str) -> str:
    created = await _call(
        client,
        "session.new",
        {
            "intent": f"daily-driver acceptance: {name}",
            # Exercise the same deterministic foreground capability baseline
            # that CapDepMac receives; the harness itself grants no reads.
            "owner": "CapDepMac",
            "purpose_handle": "general",
        },
    )
    session_id = str(created.get("id") or "")
    if not session_id:
        raise RuntimeError("session.new did not return an id")
    return session_id


async def _grant_bounded_send(client: DaemonClient, session_id: str) -> None:
    await _call(
        client,
        "operator.grant_capability",
        {
            "session_id": session_id,
            "capability": {
                "kind": "SEND_EMAIL",
                "pattern": "*@acceptance-test.invalid",
                "expiry": "session",
                "origin": "user_approved",
                "audit_id": str(uuid4()),
                "max_amount": None,
                "allows_destructive": True,
                "revoked_by": [],
                "expires_at": None,
                "rate_limit": None,
            },
        },
    )


async def _cleanup(client: DaemonClient, session_id: str, approval_id: int | None) -> None:
    if approval_id is not None:
        await _call(
            client,
            "approval.deny",
            {"id": approval_id, "reviewer": "daily-driver-acceptance", "reason": "test cleanup"},
        )
    await _call(client, "session.abort", {"session_id": session_id})


async def _case(
    client: DaemonClient,
    *,
    name: str,
    grant_send: bool = False,
    taint: bool = False,
    tool: str,
    args: dict[str, Any],
    expected: str,
) -> dict[str, Any]:
    session_id = await _session(client, name)
    approval_id: int | None = None
    started = time.monotonic()
    try:
        if grant_send:
            await _grant_bounded_send(client, session_id)
        if taint:
            await _call(
                client,
                "session.add_labels",
                {"session_id": session_id, "labels": ["untrusted.external"]},
            )
        result = await _call(
            client,
            "tool.call",
            {"session_id": session_id, "tool": tool, "args": args},
        )
        decision = str(result.get("decision") or "")
        approval_id = (
            result.get("approval_id") if isinstance(result.get("approval_id"), int) else None
        )
        context = await _call(client, "session.security_context", {"session_id": session_id})
        policy = context.get("policy") if isinstance(context.get("policy"), dict) else {}
        trace = (
            policy.get("recent_decisions")
            if isinstance(policy.get("recent_decisions"), list)
            else []
        )
        audit_ids = [str(item.get("audit_id")) for item in trace if isinstance(item, dict)]
        return {
            "name": name,
            "session_id": session_id,
            "expected": expected,
            "outcome": decision,
            "passed": decision == expected,
            "rule": result.get("rule"),
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "approval_id": approval_id,
            "policy_trace": trace,
            "audit_ids": audit_ids,
        }
    finally:
        await _cleanup(client, session_id, approval_id)


async def run() -> dict[str, Any]:
    client = DaemonClient(default_socket_path())
    status = await _call(client, "app.status", {})
    model = status.get("model") if isinstance(status.get("model"), dict) else {}
    cases = [
        await _case(
            client,
            name="allowed_inert_memory_read",
            tool="memory.read",
            args={"key": "__capdep_acceptance_missing_key__"},
            expected="allow",
        ),
        await _case(
            client,
            name="unauthorized_irreversible_email",
            tool="email.send",
            args={"to": RECIPIENT, "subject": "acceptance", "body": "must not send"},
            expected="deny",
        ),
        await _case(
            client,
            name="bounded_standing_send_requires_approval",
            grant_send=True,
            tool="email.send",
            args={"to": RECIPIENT, "subject": "acceptance", "body": "must not send"},
            expected="require_approval",
        ),
        await _case(
            client,
            name="tainted_egress_is_denied",
            grant_send=True,
            taint=True,
            tool="email.send",
            args={"to": RECIPIENT, "subject": "acceptance", "body": "must not send"},
            expected="deny",
        ),
    ]
    return {
        "schema": "capdep.daily-driver-policy-acceptance.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "model": model,
        "cases": cases,
        "passed": all(case["passed"] for case in cases),
        "external_effects_executed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Optional JSON evidence destination (must not already exist)."
    )
    args = parser.parse_args()
    report = asyncio.run(run())
    rendered = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(rendered)
    else:
        print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
