from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from tests.daemon_integration import running_daemon


class _SlowLLM:
    async def respond(self, messages, tools) -> LLMResponse:
        await anyio.sleep(10)
        return LLMResponse(content="too late", finish_reason=FinishReason.STOP)


class _TokenStreamingLLM:
    _model = "stream-test"

    async def respond_streaming(self, messages, tools, *, max_tokens=None):
        for piece in ("stream", "ed"):
            yield piece


async def _new_session(running, intent: str = "turn lifecycle") -> str:
    session = await running.client.call("session.new", {"intent": intent})
    return str(session["id"])


async def _wait_for_status(running, turn_id: str, status: str, timeout: float = 2.0) -> dict:
    deadline = anyio.current_time() + timeout
    last = {}
    while anyio.current_time() < deadline:
        last = await running.client.call("session.turn.get", {"turn_id": turn_id})
        if last["turn"]["status"] == status:
            return last
        await anyio.sleep(0.02)
    raise AssertionError(f"turn {turn_id} did not reach {status}; last={last}")


async def _consume_next(agen) -> None:
    await agen.__anext__()


async def test_llm_token_events_update_partial_content(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as running:
        running.app.llm_client = _TokenStreamingLLM()  # type: ignore[assignment]
        session_id = await _new_session(running)
        started = await running.client.call(
            "session.turn.start",
            {
                "session_id": session_id,
                "message": "hello",
                "client_id": "cli-test",
                "heartbeat_enabled": False,
            },
        )
        turn_id = started["turn"]["id"]
        done = await _wait_for_status(running, turn_id, "completed")
        assert done["turn"]["result"]["content"] == "streamed"
        events = await running.client.call("session.turn.events", {"turn_id": turn_id})
        token_events = [event for event in events["events"] if event["type"] == "llm_token"]
        assert [event["payload"]["text"] for event in token_events] == ["stream", "ed"]
        assert token_events[-1]["payload"]["partial_content"] == "streamed"


async def test_streaming_turn_completes_and_records_events(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as running:
        running.app.llm_client = FakeLLMClient(
            [LLMResponse(content="streamed hello", finish_reason=FinishReason.STOP)],
        )
        session_id = await _new_session(running)

        started = await running.client.call(
            "session.turn.start",
            {
                "session_id": session_id,
                "message": "hello",
                "client_id": "cli-test",
                "heartbeat_enabled": False,
            },
        )
        turn_id = started["turn"]["id"]

        done = await _wait_for_status(running, turn_id, "completed")

        assert done["turn"]["result"]["content"] == "streamed hello"
        events = await running.client.call("session.turn.events", {"turn_id": turn_id})
        assert [event["type"] for event in events["events"]]
        assert any(event["type"] == "completed" for event in events["events"])


async def test_turn_heartbeat_timeout_interrupts_slow_turn(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as running:
        running.app.llm_client = _SlowLLM()  # type: ignore[assignment]
        session_id = await _new_session(running)

        started = await running.client.call(
            "session.turn.start",
            {
                "session_id": session_id,
                "message": "wait",
                "client_id": "cli-test",
                "heartbeat_interval_seconds": 0.05,
                "heartbeat_timeout_seconds": 0.12,
            },
        )
        turn_id = started["turn"]["id"]

        interrupted = await _wait_for_status(running, turn_id, "interrupted")

        assert interrupted["turn"]["cancel_reason"] == "heartbeat_timeout"


async def test_turn_subscription_disconnect_cancels_registered_turn(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as running:
        running.app.llm_client = _SlowLLM()  # type: ignore[assignment]
        session_id = await _new_session(running)
        started = await running.client.call(
            "session.turn.start",
            {
                "session_id": session_id,
                "message": "wait",
                "client_id": "cli-test",
                "heartbeat_enabled": False,
            },
        )
        turn_id = started["turn"]["id"]
        stream_name = started["turn"]["stream"]

        agen = await running.client.subscribe(
            [stream_name],
            cancel_turns_on_disconnect=[turn_id],
        )
        async with anyio.create_task_group() as tg:
            tg.start_soon(_consume_next, agen)
            await anyio.sleep(0.05)
            await agen.aclose()
            tg.cancel_scope.cancel()

        interrupted = await _wait_for_status(running, turn_id, "interrupted")

        assert interrupted["turn"]["cancel_reason"] == "client_disconnect"


async def test_turn_ack_rejects_non_owner(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as running:
        running.app.llm_client = FakeLLMClient(
            [LLMResponse(content="ok", finish_reason=FinishReason.STOP)],
        )
        session_id = await _new_session(running)
        started = await running.client.call(
            "session.turn.start",
            {
                "session_id": session_id,
                "message": "hello",
                "client_id": "owner",
                "heartbeat_enabled": False,
            },
        )

        with pytest.raises(Exception, match="owned by owner"):
            await running.client.call(
                "session.turn.ack",
                {"turn_id": started["turn"]["id"], "client_id": "other"},
            )
