"""Tests for the upstream MCP supervisor.

The hard part of these tests is simulating a session death without
spinning up a real subprocess. We do that by:
  1. Constructing a `LiveSession` with a real config but no `start()`.
  2. Monkey-patching `_spawn` to install a fake session of our choice
     that lets each test script its own succeed/fail behavior.

This lets us exercise the respawn-on-death + retry-once + backoff
contract without ever touching a real MCP subprocess.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from capabledeputy.upstream.config import UpstreamServerConfig
from capabledeputy.upstream.supervisor import (
    LiveSession,
    UpstreamCallFailed,
    UpstreamDead,
    _looks_like_session_death,
)


def _make_config(name: str = "test") -> UpstreamServerConfig:
    from capabledeputy.policy.labels import LabelState

    return UpstreamServerConfig(
        name=name,
        command=("false",),  # never actually invoked in these tests
        inherent_tags=LabelState(),
        tool_overrides={},
        isolation=None,
        env={},
        strict=False,
    )


class _FakeSession:
    """Minimal stand-in for `mcp.ClientSession` — implements just the
    async methods the supervisor proxies. Each method is configurable
    per-instance to raise on demand."""

    def __init__(
        self,
        *,
        call_tool_raises: BaseException | None = None,
        call_tool_result: Any = "ok",
        list_tools_raises: BaseException | None = None,
        initialize_raises: BaseException | None = None,
    ) -> None:
        self.call_tool_raises = call_tool_raises
        self.call_tool_result = call_tool_result
        self.list_tools_raises = list_tools_raises
        self.initialize_raises = initialize_raises
        self.call_count = 0
        self.list_tools_count = 0
        self.initialize_count = 0
        self.stopped = False

    async def initialize(self) -> Any:
        self.initialize_count += 1
        if self.initialize_raises is not None:
            raise self.initialize_raises
        return "initialized"

    async def list_tools(self) -> Any:
        self.list_tools_count += 1
        if self.list_tools_raises is not None:
            raise self.list_tools_raises
        return "tools-ok"

    async def call_tool(self, name: str, arguments: Any | None = None) -> Any:
        self.call_count += 1
        if self.call_tool_raises is not None:
            raise self.call_tool_raises
        return self.call_tool_result


def _patch_spawn_sequence(live: LiveSession, fake_sessions: list[_FakeSession]) -> list[int]:
    """Replace `_spawn` so each call installs the next session from
    `fake_sessions`. Returns a list[int] whose length tracks the
    number of spawn calls."""
    spawn_calls: list[int] = []
    queue = list(fake_sessions)

    async def fake_spawn() -> None:
        # Tear down any existing session first (mirrors real behavior).
        if live._stack is not None:
            live._stack = None
        live._session = None
        if not queue:
            raise RuntimeError("test ran out of fake sessions")
        next_session = queue.pop(0)
        live._stack = None  # we don't use a real stack
        live._session = next_session  # type: ignore[assignment]
        live._consecutive_failures = 0
        spawn_calls.append(1)

    live._spawn = fake_spawn  # type: ignore[method-assign]
    return spawn_calls


# --- _looks_like_session_death heuristic ---


def test_session_death_heuristic_recognizes_broken_pipes() -> None:
    assert _looks_like_session_death(BrokenPipeError()) is True
    assert _looks_like_session_death(ConnectionResetError()) is True


def test_session_death_heuristic_treats_oserror_as_death() -> None:
    assert _looks_like_session_death(OSError("disk gone")) is True


def test_session_death_heuristic_unknown_exc_is_treated_as_death() -> None:
    """Conservative: unknown exceptions ARE treated as session death.
    Better to respawn a possibly-fine session than to leave a stuck
    one in place."""
    assert _looks_like_session_death(RuntimeError("???")) is True


def test_session_death_heuristic_http_status_error_is_not_death() -> None:
    import httpx

    err = httpx.HTTPStatusError(
        "401",
        request=httpx.Request("POST", "https://gmailmcp.googleapis.com/mcp/v1"),
        response=httpx.Response(401),
    )
    assert _looks_like_session_death(err) is False


def test_session_death_heuristic_cancelled_error_is_not_death() -> None:
    import asyncio

    assert _looks_like_session_death(asyncio.CancelledError()) is False


def test_session_death_heuristic_upstream_call_failed_is_not_death() -> None:
    assert _looks_like_session_death(UpstreamCallFailed("nope")) is False


def test_session_death_heuristic_exception_group_inspects_subexceptions() -> None:
    import httpx

    http_err = httpx.HTTPStatusError(
        "401",
        request=httpx.Request("POST", "https://example.test/mcp"),
        response=httpx.Response(401),
    )
    assert _looks_like_session_death(ExceptionGroup("eg", [http_err])) is False
    assert _looks_like_session_death(ExceptionGroup("eg", [BrokenPipeError()])) is True


# --- Initial spawn ---


def test_start_calls_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    live = LiveSession(_make_config())
    fake = _FakeSession()
    spawn_calls = _patch_spawn_sequence(live, [fake])
    asyncio.run(live.start())
    assert spawn_calls == [1]
    assert live.is_alive
    assert live._session is fake


def test_call_tool_no_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    live = LiveSession(_make_config())
    fake = _FakeSession()
    _patch_spawn_sequence(live, [fake])

    async def go() -> Any:
        await live.start()
        return await live.call_tool("x")

    result = asyncio.run(go())
    assert result == "ok"
    assert fake.call_count == 1


# --- Respawn-on-death ---


def test_call_tool_respawns_on_broken_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """First session raises BrokenPipeError → supervisor respawns →
    retry on second session succeeds."""
    live = LiveSession(_make_config(), max_backoff_seconds=0.01)
    dead = _FakeSession(call_tool_raises=BrokenPipeError("pipe dead"))
    fresh = _FakeSession(call_tool_result="recovered")
    spawn_calls = _patch_spawn_sequence(live, [dead, fresh])

    async def go() -> Any:
        await live.start()
        return await live.call_tool("x")

    result = asyncio.run(go())
    assert result == "recovered"
    # Two spawn calls: initial + respawn.
    assert len(spawn_calls) == 2
    # The dead session was called once, the fresh one once.
    assert dead.call_count == 1
    assert fresh.call_count == 1


def test_call_tool_raises_upstream_dead_when_respawn_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the respawn itself raises, the caller gets UpstreamDead."""
    live = LiveSession(_make_config(), max_backoff_seconds=0.01)
    dead = _FakeSession(call_tool_raises=BrokenPipeError("pipe dead"))

    spawn_called = []

    async def fake_spawn() -> None:
        spawn_called.append(1)
        if len(spawn_called) == 1:
            # First spawn succeeds (the initial start)
            live._session = dead  # type: ignore[assignment]
            live._consecutive_failures = 0
            return
        # Subsequent spawns (respawn-on-death) raise
        raise RuntimeError("subprocess refused to start")

    live._spawn = fake_spawn  # type: ignore[method-assign]

    async def go() -> Any:
        await live.start()
        return await live.call_tool("x")

    with pytest.raises(UpstreamDead, match="respawn failed"):
        asyncio.run(go())


def test_call_tool_raises_upstream_dead_when_retry_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Respawn succeeds but the retry call ALSO fails — we surface
    UpstreamDead instead of infinite-looping."""
    live = LiveSession(_make_config(), max_backoff_seconds=0.01)
    first = _FakeSession(call_tool_raises=BrokenPipeError("dead 1"))
    second = _FakeSession(call_tool_raises=BrokenPipeError("dead 2"))
    _patch_spawn_sequence(live, [first, second])

    async def go() -> Any:
        await live.start()
        return await live.call_tool("x")

    with pytest.raises(UpstreamDead, match="retry call also failed"):
        asyncio.run(go())


# --- Non-death exceptions pass through ---


def test_call_tool_cancelled_error_raises_upstream_call_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote HTTP failures in streamable-http MCP clients surface as
    CancelledError. That must not respawn the session or kill callers."""
    import asyncio

    live = LiveSession(_make_config(), max_backoff_seconds=0.01)
    fake = _FakeSession(call_tool_raises=asyncio.CancelledError("cancel scope"))
    spawn_calls = _patch_spawn_sequence(live, [fake])

    async def go() -> Any:
        await live.start()
        return await live.call_tool("x")

    with pytest.raises(UpstreamCallFailed, match="request cancelled"):
        asyncio.run(go())
    assert len(spawn_calls) == 1
    assert fake.call_count == 1


def test_call_tool_protocol_error_does_not_respawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary protocol error (e.g. "tool not found") should NOT
    trigger a respawn — only suspected session death does."""

    class FakeMcpError(Exception):
        """Mimics mcp.shared.exceptions.McpError. The supervisor's
        heuristic checks `type(exc).__name__ == 'McpError'` plus
        the `code` attribute."""

        def __init__(self, message: str, code: int) -> None:
            super().__init__(message)
            self.code = code

    # Rename the class to satisfy the heuristic
    FakeMcpError.__name__ = "McpError"

    live = LiveSession(_make_config(), max_backoff_seconds=0.01)
    # Use code=-32601 (method not found) — recognized as a real
    # protocol error, not death.
    proto_err = FakeMcpError("tool not found", code=-32601)
    fake = _FakeSession(call_tool_raises=proto_err)
    spawn_calls = _patch_spawn_sequence(live, [fake])

    async def go() -> Any:
        await live.start()
        await live.call_tool("nonexistent")

    with pytest.raises(FakeMcpError, match="tool not found"):
        asyncio.run(go())
    # Only the initial spawn — no respawn happened.
    assert len(spawn_calls) == 1
    assert fake.call_count == 1


# --- Concurrent callers piggyback on one respawn ---


def test_concurrent_callers_share_one_respawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two callers see the dead session, but only one respawn happens
    and both end up using the same fresh session."""
    live = LiveSession(_make_config(), max_backoff_seconds=0.001)
    dead = _FakeSession(call_tool_raises=BrokenPipeError("dead"))
    fresh = _FakeSession(call_tool_result="recovered")
    spawn_calls = _patch_spawn_sequence(live, [dead, fresh])

    async def go() -> tuple[Any, Any]:
        await live.start()
        # Both callers see `dead` as their pre_session; only one of
        # them triggers the respawn under the lock.
        a, b = await asyncio.gather(live.call_tool("a"), live.call_tool("b"))
        return a, b

    a, b = asyncio.run(go())
    assert a == b == "recovered"
    # Two spawns: initial + ONE respawn (not two).
    assert len(spawn_calls) == 2


# --- stop() is idempotent ---


def test_stop_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    live = LiveSession(_make_config())
    fake = _FakeSession()
    _patch_spawn_sequence(live, [fake])

    async def go() -> None:
        await live.start()
        await live.stop()
        await live.stop()  # second call must not raise

    asyncio.run(go())


# --- list_tools also respawns on death ---


def test_list_tools_respawns_on_death(monkeypatch: pytest.MonkeyPatch) -> None:
    live = LiveSession(_make_config(), max_backoff_seconds=0.01)
    dead = _FakeSession(list_tools_raises=BrokenPipeError("dead"))
    fresh = _FakeSession()
    _patch_spawn_sequence(live, [dead, fresh])

    async def go() -> Any:
        await live.start()
        return await live.list_tools()

    result = asyncio.run(go())
    assert result == "tools-ok"
    assert dead.list_tools_count == 1
    assert fresh.list_tools_count == 1
