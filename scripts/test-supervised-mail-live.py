"""Read-only live acceptance via daemon; prints metadata, never mailbox text.

No send capability is granted and this script never approves an action.
Run with the local daemon/model and a registered labeled IMAP provider.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.policy.capabilities import Capability, CapabilityKind


async def main():
    client = DaemonClient(default_socket_path())
    session = await client.call(
        "session.new",
        {
            "purpose_handle": "inbox",
            "owner": "live-mail-acceptance",
            "intent": "Read-only real email summary acceptance",
        },
    )
    session_id = session["id"]
    capability = Capability(
        kind=CapabilityKind.IMAP_READ,
        pattern="*",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    await client.call(
        "operator.grant_capability",
        {
            "session_id": session_id,
            "capability": capability.to_dict(),
        },
    )
    print(json.dumps({"session_id": session_id, "send_authority": False}), flush=True)
    started = time.monotonic()
    result = await client.call(
        "session.send",
        {
            "session_id": session_id,
            "max_iterations": 5,
            "message": "Summarize my two most recent real emails. Read their bodies using "
            "quarantined.summarize_mail. Do not use demo data, draft, or send. "
            "Clearly report incomplete or truncated coverage.",
        },
    )
    rows = []
    for outcome in result.get("tool_outcomes", []):
        output = outcome.get("output") or {}
        rows.append(
            {
                "tool": outcome.get("tool_name"),
                "decision": outcome.get("decision"),
                "error": bool(outcome.get("error")),
                **{
                    key: output[key]
                    for key in (
                        "ok",
                        "complete",
                        "matched_count",
                        "fetched_count",
                        "summarized_count",
                    )
                    if key in output
                },
            }
        )
    print(
        json.dumps(
            {
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "response_chars": len(result.get("content", "")),
                "finish_reason": result.get("finish_reason"),
                "tools": rows,
            }
        ),
        flush=True,
    )
    if not any(r.get("summarized_count", 0) > 0 for r in rows):
        raise SystemExit("FAIL: no actual message summary was produced")


if __name__ == "__main__":
    asyncio.run(main())
