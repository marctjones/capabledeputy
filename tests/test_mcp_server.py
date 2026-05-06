"""Tests for the MCP server. Drives a real daemon to exercise the proxy
path end-to-end without speaking the MCP wire protocol — that's the
SDK's job. We test our discover_tools and dispatch_tool functions
directly since they hold the proxy logic.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import anyio
import pytest

from capabledeputy.app import App
from capabledeputy.daemon.audit_handlers import make_audit_handlers
from capabledeputy.daemon.handlers import default_handlers
from capabledeputy.daemon.policy_handlers import make_policy_handlers
from capabledeputy.daemon.server import Daemon
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.daemon.tool_handlers import make_tool_handlers
from capabledeputy.ipc.client import DaemonClient
from capabledeputy.mcp_server.server import discover_tools, dispatch_tool
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "socket": tmp_path / "test.sock",
        "state_db": tmp_path / "state.db",
        "audit_log": tmp_path / "audit.jsonl",
    }


async def _build_daemon(paths: dict[str, Path]) -> tuple[Daemon, App]:
    app = App(
        state_db_path=paths["state_db"],
        audit_log_path=paths["audit_log"],
    )
    await app.startup()
    handlers = default_handlers()
    handlers.update(make_session_handlers(app.graph))
    handlers.update(make_audit_handlers(app.audit))
    handlers.update(make_policy_handlers())
    handlers.update(make_tool_handlers(app.registry, app.graph, app.tool_client))
    return Daemon(paths["socket"], handlers=handlers), app


async def _wait_for_socket(path: Path, timeout: float = 2.0) -> None:
    deadline = anyio.current_time() + timeout
    while anyio.current_time() < deadline:
        if path.exists():
            try:
                stream = await anyio.connect_unix(str(path))
                await stream.aclose()
                return
            except (FileNotFoundError, ConnectionRefusedError):
                pass
        await anyio.sleep(0.01)
    raise TimeoutError(f"socket {path} did not become available within {timeout}s")


async def test_discover_tools_finds_native_tools(paths: dict[str, Path]) -> None:
    daemon, _app = await _build_daemon(paths)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        tools = await discover_tools(client)
        names = {t.name for t in tools}
        assert "memory.read" in names
        assert "memory.write" in names
        assert "purchase.queue" in names

        await client.call("shutdown")


async def test_dispatch_tool_allow_returns_output(paths: dict[str, Path]) -> None:
    daemon, app = await _build_daemon(paths)
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        result = await dispatch_tool(
            client,
            s.id,
            "memory.write",
            {"key": "k", "value": "v"},
        )
        assert len(result) == 1
        text = result[0].text
        assert "ok" in text.lower()

        await client.call("shutdown")


async def test_dispatch_tool_deny_returns_policy_message(paths: dict[str, Path]) -> None:
    daemon, app = await _build_daemon(paths)
    s = await app.graph.new()

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        result = await dispatch_tool(
            client,
            s.id,
            "memory.read",
            {"key": "k"},
        )
        assert len(result) == 1
        text = result[0].text
        assert "policy denied" in text.lower()
        assert "decision=deny" in text.lower()

        await client.call("shutdown")


async def test_dispatch_tool_includes_labels_added_in_response(paths: dict[str, Path]) -> None:
    daemon, app = await _build_daemon(paths)
    app.memory.write("labs", "x", frozenset({Label.CONFIDENTIAL_HEALTH}))
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        result = await dispatch_tool(
            client,
            s.id,
            "memory.read",
            {"key": "labs"},
        )
        text = result[0].text
        assert "session labels expanded" in text
        assert "confidential.health" in text

        await client.call("shutdown")


async def test_full_mcp_scenario_blocks_egress_after_health_read(
    paths: dict[str, Path],
) -> None:
    """Simulates the canonical scenario as if Claude Code were driving
    via MCP. First call: read health data (allowed, propagates label).
    Second call: try to purchase (denied by health-meets-egress)."""
    daemon, app = await _build_daemon(paths)
    app.memory.write("rx", "lisinopril 10mg", frozenset({Label.CONFIDENTIAL_HEALTH}))
    s = await app.graph.new(intent="claude-code mcp test")
    read_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    purchase_cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=1000,
    )
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({read_cap, purchase_cap}),
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])

        read_result = await dispatch_tool(client, s.id, "memory.read", {"key": "rx"})
        assert "policy denied" not in read_result[0].text.lower()
        assert "confidential.health" in read_result[0].text

        purchase_result = await dispatch_tool(
            client,
            s.id,
            "purchase.queue",
            {"vendor": "pharmacy", "item": "rx", "amount": 50},
        )
        assert "policy denied" in purchase_result[0].text.lower()
        assert "health-meets-egress" in purchase_result[0].text

        await client.call("shutdown")


async def test_build_server_constructs_a_server(paths: dict[str, Path]) -> None:
    from uuid import uuid4

    from capabledeputy.mcp_server.server import build_server

    daemon, _app = await _build_daemon(paths)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        server = await build_server(client, uuid4())
        assert server.name == "capdep"

        await client.call("shutdown")
