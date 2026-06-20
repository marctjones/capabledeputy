from __future__ import annotations

import mcp.types as mcp_types

from capabledeputy.mcp_server.control import (
    build_control_server,
    discover_control_tools,
    dispatch_control_tool,
)


def _text(result: mcp_types.CallToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, mcp_types.TextContent)
    return content.text


def test_control_tools_include_daemon_client_surface() -> None:
    tools = discover_control_tools()
    names = {tool.name for tool in tools}

    assert "capdep_ping" in names
    assert "app_status" in names
    assert "session_list" in names
    assert "session_new" in names
    assert "tool_call" in names
    assert "approval_approve" in names
    assert "setup_status" in names
    assert "gmail_oauth_login" in names
    assert "provenance_graph" in names

    tool_call = next(tool for tool in tools if tool.name == "tool_call")
    assert tool_call.annotations is not None
    assert tool_call.annotations.readOnlyHint is False
    assert tool_call.annotations.destructiveHint is True
    assert tool_call.annotations.openWorldHint is True

    app_status = next(tool for tool in tools if tool.name == "app_status")
    assert app_status.annotations is not None
    assert app_status.annotations.readOnlyHint is True

    for tool in tools:
        assert tool.outputSchema is not None
        assert tool.meta is not None
        assert tool.meta["io.capabledeputy/surface"] == "control"
        assert tool.meta["io.capabledeputy/session_bound"] is False


async def test_control_status_dispatches_to_daemon(fake_daemon) -> None:
    client = fake_daemon({"app.status": {"daemon": "running"}})

    result = await dispatch_control_tool(client, "app_status")

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["daemon"] == "running"
    assert result.meta is not None
    assert result.meta["io.capabledeputy/surface"] == "control"
    assert client.calls == [("app.status", None)]


async def test_control_session_new_dispatches_params(fake_daemon) -> None:
    client = fake_daemon({"session.new": {"id": "s1"}})

    result = await dispatch_control_tool(
        client,
        "session_new",
        {
            "owner": "codex",
            "intent": "review inbox",
            "purpose_handle": "personal_assistant",
            "labels": ["low", "user"],
            "first_use_prompts": False,
        },
    )

    assert result.isError is False
    assert client.calls == [
        (
            "session.new",
            {
                "owner": "codex",
                "intent": "review inbox",
                "purpose_handle": "personal_assistant",
                "labels": ["low", "user"],
                "first_use_prompts": False,
            },
        ),
    ]


async def test_control_tool_call_dispatches_policy_gated_call(fake_daemon) -> None:
    client = fake_daemon({"tool.call": {"queued_approval": True}})

    result = await dispatch_control_tool(
        client,
        "tool_call",
        {
            "session_id": "s1",
            "tool": "gmail.send",
            "args": {"to": "person@example.com"},
        },
    )

    assert result.isError is False
    assert "queued_approval" in _text(result)
    assert client.calls == [
        (
            "tool.call",
            {
                "session_id": "s1",
                "tool": "gmail.send",
                "args": {"to": "person@example.com"},
            },
        ),
    ]


async def test_control_approval_approve_dispatches(fake_daemon) -> None:
    client = fake_daemon({"approval.approve": {"status": "approved"}})

    result = await dispatch_control_tool(
        client,
        "approval_approve",
        {"id": 42},
    )

    assert result.isError is False
    assert client.calls == [
        ("approval.approve", {"id": 42, "decided_by": "mcp-control"}),
    ]


async def test_control_gmail_oauth_login_dispatches(fake_daemon) -> None:
    client = fake_daemon({"setup.google_gmail.oauth_login": {"token_configured": True}})

    result = await dispatch_control_tool(
        client,
        "gmail_oauth_login",
        {"open_browser": True, "timeout_seconds": 90},
    )

    assert result.isError is False
    assert client.calls == [
        (
            "setup.google_gmail.oauth_login",
            {"open_browser": True, "timeout_seconds": 90},
        ),
    ]


async def test_control_unknown_tool_is_error(fake_daemon) -> None:
    client = fake_daemon({})

    result = await dispatch_control_tool(client, "missing")

    assert result.isError is True
    assert "unknown control tool" in _text(result)


async def test_build_control_server_constructs_server(fake_daemon) -> None:
    server = await build_control_server(fake_daemon({}))

    assert server.name == "capdep-control"
