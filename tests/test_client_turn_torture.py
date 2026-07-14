"""Daemon-client turn torture tests.

These tests drive the daemon through the real Unix-socket client and
stress the turn loop with success, tool use, LLM failure, context
overflow, thrash detection, and iteration-cap termination cases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

import anyio
import pytest

from capabledeputy.agent import loop as loop_mod
from capabledeputy.app import App
from capabledeputy.daemon.server import Daemon
from capabledeputy.ipc.client import DaemonClient, DaemonError
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from tests.daemon_integration import (
    DaemonTestPaths,
    build_test_handlers,
    daemon_test_paths,
    wait_for_socket,
)


class _RaisingLLM:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def respond(self, messages, tools) -> LLMResponse:
        raise self._exc


@dataclass(frozen=True)
class TurnTortureCase:
    name: str
    llm: object
    expected_content: str | None = None
    expected_finish_reason: str | None = None
    expected_error: str | None = None
    grant_write_fs: bool = False
    patch_context_window: int | None = None
    max_iterations: int | None = None
    expected_tool_count: int | None = None


@asynccontextmanager
async def live_daemon(
    tmp_path: Path,
    llm_client: object,
) -> AsyncIterator[tuple[App, DaemonClient, DaemonTestPaths]]:
    paths = daemon_test_paths(tmp_path)
    app = App(
        state_db_path=paths.state_db,
        audit_log_path=paths.audit_log,
        llm_client=llm_client,  # pyright: ignore[reportArgumentType]
    )
    await app.startup()
    daemon = Daemon(paths.socket, handlers=build_test_handlers(app, paths))
    unsubscribe = app.audit.subscribe(
        lambda event: daemon.publish("audit", event.to_dict()),
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await wait_for_socket(paths.socket)
        client = DaemonClient(paths.socket)
        try:
            yield app, client, paths
        finally:
            with suppress(Exception):
                await client.call("shutdown")
            unsubscribe()
            tg.cancel_scope.cancel()


async def _new_session(client: DaemonClient, intent: str) -> str:
    session = await client.call("session.new", {"intent": intent})
    return str(session["id"])


async def _grant_write_fs(client: DaemonClient, session_id: str) -> None:
    await client.call(
        "session.grant_capability",
        {
            "session_id": session_id,
            "capability": Capability(kind=CapabilityKind.WRITE_FS, pattern="*").to_dict(),
        },
    )


CASES: tuple[TurnTortureCase, ...] = (
    TurnTortureCase(
        name="clean_final_answer",
        llm=FakeLLMClient(
            [LLMResponse(content="hello from capdep", finish_reason=FinishReason.STOP)],
        ),
        expected_content="hello from capdep",
        expected_finish_reason="stop",
    ),
    TurnTortureCase(
        name="tool_then_final_answer",
        llm=FakeLLMClient(
            [
                LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="memory.write",
                            args={"key": "torture", "value": "ok"},
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(content="stored", finish_reason=FinishReason.STOP),
            ],
        ),
        expected_content="stored",
        expected_finish_reason="stop",
        grant_write_fs=True,
        expected_tool_count=1,
    ),
    TurnTortureCase(
        name="llm_error",
        llm=_RaisingLLM(RuntimeError("simulated provider 503")),
        expected_content="[turn interrupted: llm_error:RuntimeError]",
        expected_finish_reason="length",
        expected_error=None,
    ),
    TurnTortureCase(
        name="context_overflow",
        llm=FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)]),
        expected_content="[turn interrupted: context_overflow]",
        expected_finish_reason="length",
        patch_context_window=1,
    ),
    TurnTortureCase(
        name="max_iterations",
        llm=FakeLLMClient(
            [
                LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(id=f"c{i}", name="memory.write", args={"key": str(i), "value": i}),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                )
                for i in range(3)
            ],
        ),
        expected_error="exceeded 2 iterations",
        grant_write_fs=True,
        max_iterations=2,
    ),
    TurnTortureCase(
        name="thrash_detection",
        llm=FakeLLMClient(
            [
                LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(id="c1", name="memory.write", args={"key": "t", "value": "x"}),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(id="c2", name="memory.write", args={"key": "t", "value": "x"}),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(id="c3", name="memory.write", args={"key": "t", "value": "x"}),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            ],
        ),
        expected_error="thrashing",
        grant_write_fs=True,
        max_iterations=10,
    ),
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_session_send_torture_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: TurnTortureCase,
) -> None:
    if case.patch_context_window is not None:
        monkeypatch.setattr(
            loop_mod, "_context_window_for", lambda model: case.patch_context_window
        )

    async with live_daemon(tmp_path, case.llm) as (_app, client, _paths):
        session_id = await _new_session(client, case.name)
        if case.grant_write_fs:
            await _grant_write_fs(client, session_id)

        params: dict[str, object] = {"session_id": session_id, "message": case.name}
        if case.max_iterations is not None:
            params["max_iterations"] = case.max_iterations

        if case.expected_error is not None:
            with pytest.raises(DaemonError, match=case.expected_error):
                await client.call("session.send", params)
            return

        result = await client.call("session.send", params)
        assert result["content"] == case.expected_content
        assert result["finish_reason"] == case.expected_finish_reason
        if case.expected_tool_count is not None:
            assert len(result["tool_outcomes"]) == case.expected_tool_count
