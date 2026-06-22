from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from capabledeputy.ipc.client import DaemonClient, DaemonError
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from tests.test_client_turn_torture import live_daemon


async def test_session_send_claims_workstream_and_rejects_other_client(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)])

    async with live_daemon(tmp_path, llm) as (_app, client1, paths):
        client2 = DaemonClient(paths.socket)
        session = await client1.call("session.new", {"intent": "workstream ownership"})

        first = await client1.call(
            "session.send",
            {
                "session_id": session["id"],
                "message": "first",
                "client_id": "gui-a",
            },
        )
        assert first["workstream"]["client_id"] == "gui-a"

        state = await client1.call("daemon.state")
        interactive = next(
            workflow
            for workflow in state["workflows"]["interactive"]
            if workflow["session_id"] == session["id"]
        )
        assert interactive["workstream_client_id"] == "gui-a"
        assert interactive["workstream_status"] == "active"

        with pytest.raises(DaemonError, match="owned by gui-a"):
            await client2.call(
                "session.send",
                {
                    "session_id": session["id"],
                    "message": "second",
                    "client_id": "gui-b",
                },
            )


async def test_workstream_release_allows_reclaim_by_another_client(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)])

    async with live_daemon(tmp_path, llm) as (_app, client1, paths):
        client2 = DaemonClient(paths.socket)
        session = await client1.call("session.new", {"intent": "workstream release"})

        claimed = await client1.call(
            "workstream.claim",
            {
                "session_id": session["id"],
                "client_id": "gui-a",
                "reason": "editing",
            },
        )
        workstream = claimed["workstream"]

        released = await client1.call(
            "workstream.release",
            {
                "workstream_id": workstream["id"],
                "client_id": "gui-a",
                "lease_token": workstream["lease_token"],
            },
        )
        assert released["workstream"]["status"] == "released"

        second = await client2.call(
            "workstream.claim",
            {
                "session_id": session["id"],
                "client_id": "gui-b",
            },
        )
        assert second["workstream"]["client_id"] == "gui-b"


async def test_workstream_release_requires_token_even_for_same_client(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)])

    async with live_daemon(tmp_path, llm) as (_app, client, _paths):
        session = await client.call("session.new", {"intent": "strict release"})
        claimed = await client.call(
            "workstream.claim",
            {
                "session_id": session["id"],
                "client_id": "gui-a",
            },
        )

        with pytest.raises(DaemonError, match="owned by gui-a"):
            await client.call(
                "workstream.release",
                {
                    "workstream_id": claimed["workstream"]["id"],
                    "client_id": "gui-a",
                },
            )


async def test_admin_override_can_take_over_and_cancel_workstream(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)])

    async with live_daemon(tmp_path, llm) as (app, client, _paths):
        session = await client.call("session.new", {"intent": "admin takeover"})
        first = await client.call(
            "workstream.claim",
            {
                "session_id": session["id"],
                "client_id": "gui-a",
            },
        )

        second = await client.call(
            "workstream.claim",
            {
                "session_id": session["id"],
                "client_id": "gui-b",
                "admin_override": True,
                "reason": "operator takeover",
            },
        )
        assert second["workstream"]["client_id"] == "gui-b"
        assert second["workstream"]["id"] != first["workstream"]["id"]

        app.cancellation_flags[UUID(session["id"])] = False
        cancelled = await client.call(
            "session.cancel",
            {
                "session_id": session["id"],
                "client_id": "operator",
                "admin_override": True,
            },
        )
        assert cancelled == {"cancelled": True}


async def test_expired_workstream_can_be_reclaimed(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)])

    async with live_daemon(tmp_path, llm) as (_app, client, _paths):
        session = await client.call("session.new", {"intent": "lease expiry"})
        first = await client.call(
            "workstream.claim",
            {
                "session_id": session["id"],
                "client_id": "gui-a",
                "lease_seconds": 1,
            },
        )

        import anyio

        await anyio.sleep(1.1)
        expired = await client.call("workstream.sweep_expired")
        assert [item["id"] for item in expired["workstreams"]] == [first["workstream"]["id"]]

        second = await client.call(
            "workstream.claim",
            {
                "session_id": session["id"],
                "client_id": "gui-b",
            },
        )
        assert second["workstream"]["client_id"] == "gui-b"


async def test_release_client_retires_active_workstreams(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)])

    async with live_daemon(tmp_path, llm) as (_app, client, _paths):
        session1 = await client.call("session.new", {"intent": "client cleanup 1"})
        session2 = await client.call("session.new", {"intent": "client cleanup 2"})
        for session in (session1, session2):
            await client.call(
                "workstream.claim",
                {
                    "session_id": session["id"],
                    "client_id": "gui-a",
                },
            )

        released = await client.call(
            "workstream.release_client",
            {
                "client_id": "gui-a",
                "reason": "heartbeat lost",
            },
        )
        assert len(released["workstreams"]) == 2
        assert {item["status"] for item in released["workstreams"]} == {"released"}

        state = await client.call("daemon.state")
        assert state["workstreams"]["active_count"] == 0
