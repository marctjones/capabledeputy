from __future__ import annotations

import mcp.types as mcp_types

from capabledeputy.mcp_server.admin import discover_admin_tools, dispatch_admin_tool


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
    assert status.annotations.readOnlyHint is True
    for tool in tools:
        assert tool.outputSchema is not None
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

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["workflow_ready"] is True
    assert result.meta is not None
    assert result.meta["io.capabledeputy/surface"] == "admin"
    assert client.calls == [("setup.status", None)]


async def test_admin_unknown_tool_is_error(fake_daemon) -> None:
    client = fake_daemon({})

    result = await dispatch_admin_tool(client, "missing")

    assert result.isError is True
    assert "unknown admin tool" in _text(result)
