from __future__ import annotations

from pathlib import Path

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
