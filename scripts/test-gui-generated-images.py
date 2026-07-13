#!/usr/bin/env python3
"""E2E: daemon chat turn must call image.generate and return inline markdown."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK_IMAGES = Path.home() / ".capdep/work/images"

CASES = (
    ("dog", "generate and show me a picture of a dog"),
    (
        "attractive_woman",
        "generate and show me a picture of an attractive woman",
    ),
)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"OK  {msg}")


def extract_image_path(content: str) -> str | None:
    m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", content)
    return m.group(1).strip() if m else None


async def run_turn(client, message: str, label: str) -> dict:
    created = await client.call(
        "session.new",
        {
            "intent": f"gui generated image e2e: {label}",
            "owner": "CapDepMac",
            "purpose_handle": "general",
        },
    )
    session_id = str(created.get("id") or "")
    if not session_id:
        fail("session.new returned no id")

    started = await client.call(
        "session.turn.start",
        {
            "session_id": session_id,
            "message": message,
            "client_id": "CapDepMac",
            "heartbeat_timeout_seconds": 120,
        },
    )
    turn_id = str((started.get("turn") or {}).get("id") or "")
    if not turn_id:
        fail("session.turn.start returned no turn id")

    tools_seen: int | None = None
    tool_names: list[str] = []
    content = ""
    cursor = 0
    for _ in range(600):
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
            if etype == "tool_dispatched":
                name = str(
                    payload.get("tool_name") or payload.get("tool") or payload.get("name") or ""
                )
                if name:
                    tool_names.append(name)
            if etype == "tool_returned":
                outcome = payload.get("outcome") or {}
                name = str(
                    outcome.get("tool_name")
                    or payload.get("tool_name")
                    or payload.get("tool")
                    or payload.get("name")
                    or ""
                )
                decision = str(outcome.get("decision") or payload.get("decision") or "")
                if name:
                    tool_names.append(f"{name}:{decision}")
            if etype == "llm_token" and payload.get("partial_content"):
                content = str(payload["partial_content"])
        if status in {"completed", "interrupted", "error"}:
            result = (observed.get("turn") or {}).get("result") or {}
            content = str(result.get("content") or content or "")
            if status == "error":
                fail(f"{label} turn error: {(observed.get('turn') or {}).get('error')}")
            if status == "interrupted":
                fail(f"{label} turn interrupted: {content[:300]!r}")
            break
        await asyncio.sleep(0.25)
    else:
        fail(f"{label} turn timed out")

    return {
        "label": label,
        "session_id": session_id,
        "turn_id": turn_id,
        "tools_seen": tools_seen,
        "tool_names": tool_names,
        "content": content,
    }


async def main() -> None:
    from capabledeputy.ipc.client import DaemonClient
    from capabledeputy.ipc.socket_path import default_socket_path

    client = DaemonClient(default_socket_path())
    try:
        await client.call("ping", {})
    except Exception as exc:
        fail(f"daemon not reachable: {exc}")
    ok("daemon ping")

    paths: list[str] = []
    for label, message in CASES:
        print(f"\n--- {label} ---")
        outcome = await run_turn(client, message, label)
        content = outcome["content"]
        tools_seen = outcome["tools_seen"]
        tool_blob = " ".join(outcome["tool_names"]).lower()

        if tools_seen == 0:
            fail(f"{label}: routed as conversational (0 tools)")
        ok(f"{label}: n_tools={tools_seen}")

        if "image.generate" not in tool_blob:
            fail(
                f"{label}: image.generate not called; tools={outcome['tool_names']!r}\n"
                f"content={content[:400]!r}",
            )
        ok(f"{label}: image.generate dispatched")

        if "torch" in content.lower():
            fail(f"{label}: torch error in reply: {content[:300]!r}")
        if any(
            phrase in content.lower()
            for phrase in (
                "cannot generate",
                "can't generate",
                "unable to generate",
                "inappropriate",
                "real people",
            )
        ):
            fail(f"{label}: model refused in prose: {content[:400]!r}")

        img_path = extract_image_path(content)
        if not img_path:
            fail(f"{label}: no ![...](path) in content: {content[:400]!r}")

        expanded = str(Path(img_path).expanduser())
        p = Path(expanded)
        if not p.is_file():
            fail(f"{label}: image path missing: {expanded}")
        if p.stat().st_size < 1000:
            fail(f"{label}: image too small: {p.stat().st_size}")
        if WORK_IMAGES not in p.parents and "work/images" not in str(p):
            fail(f"{label}: unexpected image path (not work/images): {expanded}")

        ok(f"{label}: markdown path {expanded} ({p.stat().st_size} bytes)")
        paths.append(expanded)

    print("\nPASS generated-image daemon e2e (dog + attractive woman)")
    for p in paths:
        print(f"  {p}")


if __name__ == "__main__":
    asyncio.run(main())
