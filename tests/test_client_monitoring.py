"""Live client monitoring tests.

These tests exercise the daemon the way a real interactive client would:
subscribe to audit events, inspect shared state, and tolerate multiple
clients talking to the same local IPC server at once.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import anyio

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from tests.test_client_turn_torture import live_daemon


async def test_client_subscribe_receives_audit_events(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)])

    async with live_daemon(tmp_path, llm) as (_app, client, _paths):
        events = await client.subscribe(["audit"])
        event_box: dict[str, dict[str, object]] = {}

        async def _read_first_event() -> None:
            event_box["event"] = await anext(events)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_read_first_event)
            await anyio.sleep(0.05)
            await client.call("session.new", {"intent": "audit-subscribe-smoke"})

        event = event_box["event"]

        assert event["stream"] == "audit"
        assert event["data"]["event_type"] == "session.created"
        assert event["data"]["payload"]["intent"] == "audit-subscribe-smoke"
        await events.aclose()


async def test_two_clients_can_interact_with_same_daemon_state(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)])

    async with live_daemon(tmp_path, llm) as (_app, client1, paths):
        client2 = DaemonClient(paths.socket)
        session = await client1.call("session.new", {"intent": "multi-client"})

        results: dict[str, dict[str, object]] = {}

        async def _get_session() -> None:
            results["get"] = await client1.call("session.get", {"session_id": session["id"]})

        async def _list_sessions() -> None:
            results["list"] = await client2.call("session.list", {})

        async with anyio.create_task_group() as tg:
            tg.start_soon(_get_session)
            tg.start_soon(_list_sessions)

        assert results["get"]["id"] == session["id"]
        listed = cast(list[dict[str, object]], results["list"]["sessions"])
        assert any(s["id"] == session["id"] for s in listed)
