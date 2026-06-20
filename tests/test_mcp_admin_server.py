from __future__ import annotations

import mcp.types as mcp_types

from capabledeputy.mcp_server.admin import discover_admin_tools, dispatch_admin_tool


def _text(result: mcp_types.CallToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, mcp_types.TextContent)
    return content.text


def test_admin_tools_include_gmail_oauth_setup() -> None:
    tools = discover_admin_tools()
    names = {tool.name for tool in tools}
    assert "setup_status" in names
    assert "gmail_oauth_status" in names
    assert "gmail_configure_oauth_client" in names
    assert "gmail_oauth_login" in names

    configure = next(tool for tool in tools if tool.name == "gmail_configure_oauth_client")
    assert configure.annotations is not None
    assert configure.annotations.readOnlyHint is False
    assert configure.inputSchema["required"] == ["client_id", "client_secret"]
    for tool in tools:
        assert tool.outputSchema is not None
        assert tool.meta is not None
        assert tool.meta["io.capabledeputy/surface"] == "admin"
        assert tool.meta["io.capabledeputy/session_bound"] is False


async def test_admin_gmail_status_dispatches_to_daemon(fake_daemon) -> None:
    client = fake_daemon(
        {
            "setup.google_gmail.oauth_status": {
                "server": "google-gmail",
                "configured": False,
            },
        },
    )

    result = await dispatch_admin_tool(client, "gmail_oauth_status")

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["server"] == "google-gmail"
    assert result.meta is not None
    assert result.meta["io.capabledeputy/surface"] == "admin"
    assert client.calls == [("setup.google_gmail.oauth_status", None)]


async def test_admin_configure_gmail_oauth_dispatches_to_daemon(fake_daemon) -> None:
    client = fake_daemon(
        {
            "setup.google_gmail.configure_oauth": {
                "server": "google-gmail",
                "configured": True,
            },
        },
    )

    result = await dispatch_admin_tool(
        client,
        "gmail_configure_oauth_client",
        {"client_id": "id", "client_secret": "secret"},
    )

    assert result.isError is False
    assert "google-gmail" in _text(result)
    assert client.calls == [
        (
            "setup.google_gmail.configure_oauth",
            {"client_id": "id", "client_secret": "secret"},
        ),
    ]


async def test_admin_gmail_login_dispatches_to_daemon(fake_daemon) -> None:
    client = fake_daemon(
        {
            "setup.google_gmail.oauth_login": {
                "server": "google-gmail",
                "token_configured": True,
            },
        },
    )

    result = await dispatch_admin_tool(
        client,
        "gmail_oauth_login",
        {"open_browser": True, "timeout_seconds": 60},
    )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["token_configured"] is True
    assert client.calls == [
        (
            "setup.google_gmail.oauth_login",
            {"open_browser": True, "timeout_seconds": 60},
        ),
    ]


async def test_admin_unknown_tool_is_error(fake_daemon) -> None:
    client = fake_daemon({})

    result = await dispatch_admin_tool(client, "missing")

    assert result.isError is True
    assert "unknown admin tool" in _text(result)
