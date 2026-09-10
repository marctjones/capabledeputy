from __future__ import annotations

import mcp.types as mcp_types

from capabledeputy.mcp_server.admin import (
    build_admin_server,
    discover_admin_tools,
    dispatch_admin_tool,
)
from tests.mcp_conformance import create_connected_server_and_client_session


def _text(result: mcp_types.CallToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, mcp_types.TextContent)
    return content.text


def test_admin_tools_include_setup_status() -> None:
    tools = discover_admin_tools()
    names = {tool.name for tool in tools}
    assert "setup_status" in names

    status = next(tool for tool in tools if tool.name == "setup_status")
    assert status.annotations is not None
    assert status.annotations.read_only_hint is True
    for tool in tools:
        assert tool.output_schema is not None
        assert tool.meta is not None
        assert tool.meta["io.capabledeputy/surface"] == "admin"
        assert tool.meta["io.capabledeputy/session_bound"] is False


async def test_admin_setup_status_dispatches_to_daemon(fake_daemon) -> None:
    client = fake_daemon(
        {
            "setup.status": {
                "workflow_ready": True,
                "blocking_steps": [],
            },
        },
    )

    result = await dispatch_admin_tool(client, "setup_status")

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["workflow_ready"] is True
    assert result.meta is not None
    assert result.meta["io.capabledeputy/surface"] == "admin"
    assert client.calls == [("setup.status", None)]


async def test_admin_unknown_tool_is_error(fake_daemon) -> None:
    client = fake_daemon({})

    result = await dispatch_admin_tool(client, "missing")

    assert result.is_error is True
    assert "unknown admin tool" in _text(result)


async def test_admin_dispatch_reports_daemon_call_error(fake_daemon) -> None:
    client = fake_daemon().raises("setup.status", RuntimeError("daemon unreachable"))

    result = await dispatch_admin_tool(client, "setup_status")

    assert result.is_error is True
    assert "daemon unreachable" in _text(result)


async def test_build_admin_server_serves_tools_over_a_real_session(fake_daemon) -> None:
    client = fake_daemon(
        {
            "setup.status": {"workflow_ready": True, "blocking_steps": []},
        },
    )
    server = await build_admin_server(client)

    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == {"setup_status"}

        result = await session.call_tool("setup_status", {})
        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["workflow_ready"] is True
