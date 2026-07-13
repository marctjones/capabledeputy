#!/usr/bin/env python3
"""End-to-end check: demo image exists, daemon turn returns real markdown path."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO_PATH = Path.home() / "Library/Application Support/CapDep/media/demo-cat.jpg"
DEMO_FALLBACK = REPO / "apps/macos/CapDep/.build/demo-cat.jpg"
MESSAGE = "Show me the demo cat image inline"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"OK  {msg}")


def resolved_demo_path() -> Path:
    if DEMO_PATH.is_file():
        return DEMO_PATH
    if DEMO_FALLBACK.is_file():
        return DEMO_FALLBACK
    fail(f"demo image missing: {DEMO_PATH} (also checked {DEMO_FALLBACK})")
    raise AssertionError("unreachable")


async def main() -> None:
    demo = resolved_demo_path()
    if demo.stat().st_size < 1000:
        fail(f"demo image too small: {demo.stat().st_size}")
    ok(f"demo image exists ({demo.stat().st_size} bytes, JPEG)")

    demo_env = os.environ.get("CAPDEP_DEMO_IMAGE", "").strip()
    if demo_env and demo_env != str(demo):
        print(f"WARN CAPDEP_DEMO_IMAGE={demo_env!r} (expected {demo})")
    elif not demo_env:
        os.environ["CAPDEP_DEMO_IMAGE"] = str(demo)
        print("NOTE set CAPDEP_DEMO_IMAGE for this test run")

    from capabledeputy.ipc.client import DaemonClient
    from capabledeputy.ipc.socket_path import default_socket_path

    client = DaemonClient(default_socket_path())
    try:
        await client.call("ping", {})
    except Exception as exc:
        fail(f"daemon not reachable: {exc}")
    ok("daemon ping")

    created = await client.call(
        "session.new",
        {
            "intent": "gui inline image e2e",
            "owner": "CapDepMac",
            "purpose_handle": "general",
        },
    )
    session_id = str(created.get("id") or "")
    if not session_id:
        fail("session.new returned no id")
    ok(f"session.new {session_id[:8]}")

    started = await client.call(
        "session.turn.start",
        {
            "session_id": session_id,
            "message": MESSAGE,
            "client_id": "CapDepMac",
        },
    )
    turn = started.get("turn") or {}
    turn_id = str(turn.get("id") or "")
    if not turn_id:
        fail("session.turn.start returned no turn id")
    ok(f"session.turn.start {turn_id[:8]}")

    tools_seen: int | None = None
    image_events: list[dict] = []
    content = ""
    cursor = 0
    for _ in range(300):
        observed = await client.call("session.turn.get", {"turn_id": turn_id})
        status = str((observed.get("turn") or {}).get("status") or "")
        events = await client.call(
            "session.turn.events",
            {"turn_id": turn_id, "cursor": cursor, "limit": 200},
        )
        cursor = int(events.get("next_cursor") or cursor)
        for event in events.get("events") or []:
            etype = event.get("type")
            payload = event.get("payload") or {}
            if etype == "llm_request_sent":
                tools_seen = int(payload.get("n_tools") or 0)
            if etype == "image_attachment":
                image_events.append(payload)
            if etype == "llm_token" and payload.get("partial_content"):
                content = str(payload["partial_content"])
        if status in {"completed", "interrupted", "error"}:
            result = (observed.get("turn") or {}).get("result") or {}
            content = str(result.get("content") or content or "")
            if status == "error":
                fail(f"turn error: {(observed.get('turn') or {}).get('error')}")
            break
        await asyncio.sleep(0.25)
    else:
        fail("turn timed out")

    ok(f"turn completed ({len(content)} chars)")
    if tools_seen is not None:
        ok(f"llm_request_sent n_tools={tools_seen}")
        if tools_seen == 0:
            fail("image request routed as conversational (0 tools)")

    real = str(demo)
    if "![" not in content or "](" not in content:
        fail(f"assistant content lacks markdown image syntax: {content[:200]!r}")
    if real not in content:
        fail(f"assistant content missing real demo path.\ncontent={content!r}")
    if "/absolute/path" in content:
        fail(f"assistant used placeholder path.\ncontent={content!r}")
    ok(f"markdown image references real path: {real}")

    token_events = 0
    events = await client.call(
        "session.turn.events",
        {"turn_id": turn_id, "cursor": 0, "limit": 500},
    )
    for event in events.get("events") or []:
        if event.get("type") == "llm_token":
            token_events += 1
            partial = (event.get("payload") or {}).get("partial_content") or ""
            if real in partial:
                ok("llm_token stream carries image markdown to client")
                break
    if token_events == 0:
        fail("no llm_token events returned to client")
    ok(f"llm_token events: {token_events}")

    if image_events:
        ok(f"image_attachment events: {len(image_events)}")
    else:
        print("NOTE no image_attachment events (expected for pure LLM markdown reply)")

    print("PASS gui inline image daemon→client contract")


if __name__ == "__main__":
    asyncio.run(main())
