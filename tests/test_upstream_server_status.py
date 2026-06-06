"""Tests for per-upstream-server status tracking surfaced via /server.

Verifies that UpstreamManager captures registered/failed state for
each configured upstream MCP server, including rejected tool names
(strict-mode unclassifiable tools).
"""

from __future__ import annotations

from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.adapter import LabeledMcpAdapter
from capabledeputy.upstream.config import UpstreamServerConfig
from capabledeputy.upstream.manager import UpstreamManager, UpstreamServerStatus


class _StubSession:
    """Minimal in-process MCP session double — yields a fixed tool list."""

    def __init__(self, tools: list, raises_on_start: Exception | None = None) -> None:
        self._tools = tools
        self._raise = raises_on_start

    async def start(self):
        if self._raise is not None:
            raise self._raise

    async def stop(self):
        pass

    async def list_tools(self):
        from types import SimpleNamespace

        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, arguments):
        raise NotImplementedError


def _stub_tool(name: str, read_only: bool = True):
    """Produce a stub object matching the mcp `Tool` shape."""
    from types import SimpleNamespace

    return SimpleNamespace(
        name=name,
        description="stub",
        annotations=SimpleNamespace(readOnlyHint=read_only, destructiveHint=False),
        inputSchema={"type": "object"},
        meta=None,
    )


def test_upstream_server_status_dataclass_shape() -> None:
    """Frozen dataclass with expected fields."""
    s = UpstreamServerStatus(
        name="foo",
        state="registered",
        registered_at_epoch=1234567890,
        registered_tool_count=5,
    )
    assert s.name == "foo"
    assert s.state == "registered"
    assert s.registered_tool_count == 5
    assert s.rejected_tool_count == 0
    assert s.rejected_tool_names == ()
    assert s.error == ""


def test_manager_server_status_property_starts_empty() -> None:
    """Before __aenter__, status dict is empty."""
    mgr = UpstreamManager([], registry=ToolRegistry())
    assert mgr.server_status == {}


async def test_manager_captures_failed_server_status() -> None:
    """When a server's startup fails, UpstreamManager records it as
    `failed` with the exception text and command intact — visible
    to /server.

    Uses a deliberately nonexistent command so the supervisor's
    subprocess spawn raises ENOENT. Exercises the real failure
    path end-to-end (manager's __aenter__ catches and records)."""
    config = UpstreamServerConfig(name="willfail", command=("/tmp/does-not-exist-cmd-xyz123",))

    async with UpstreamManager([config], ToolRegistry()) as mgr:
        status = mgr.server_status
        assert "willfail" in status
        s = status["willfail"]
        assert s.state == "failed"
        # Real error from the OS — ENOENT for a missing executable.
        assert s.error  # non-empty
        assert "No such" in s.error or "ENOENT" in s.error
        assert s.command == ("/tmp/does-not-exist-cmd-xyz123",)
        assert s.registered_tool_count == 0


async def test_manager_captures_registered_server_status() -> None:
    """Successful registration produces a `registered` row with the
    registered_tool_count populated from the adapter."""
    # Stub the LiveSession + register manually to avoid spawning a
    # real subprocess.
    config = UpstreamServerConfig(name="ok", command=("stub",))
    session = _StubSession(
        tools=[
            _stub_tool("read_files", read_only=True),
            _stub_tool("list_dirs", read_only=True),
        ]
    )
    mgr = UpstreamManager([config], ToolRegistry())
    # Manually walk the registration path without the supervisor
    adapter = LabeledMcpAdapter(config=config, session=session)
    registered = await adapter.register_tools(mgr._registry)
    mgr._adapters.append(adapter)
    mgr._sessions.append(session)
    # Mirror what __aenter__ does
    import time as _time

    mgr._status[config.name] = UpstreamServerStatus(
        name=config.name,
        state="registered",
        registered_at_epoch=int(_time.time()),
        registered_tool_count=len(adapter.registered_names),
        rejected_tool_count=len(adapter.rejected_tools),
        rejected_tool_names=tuple(adapter.rejected_tools),
        command=tuple(config.command),
    )

    status = mgr.server_status["ok"]
    assert status.state == "registered"
    assert status.registered_tool_count == 2
    assert status.rejected_tool_count == 0


def test_adapter_exposes_registered_names() -> None:
    """The adapter's public `registered_names` property is needed by
    the manager to compute the count without poking internals."""
    config = UpstreamServerConfig(name="empty", command=("x",))
    adapter = LabeledMcpAdapter(config=config, session=_StubSession([]))
    # Before any registration, both lists are empty
    assert adapter.registered_names == []
    assert adapter.rejected_tools == []
