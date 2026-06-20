"""Admin MCP server for local CapableDeputy setup operations.

This is intentionally separate from `capdep mcp-server --session-id ...`.
The session-bound server exposes normal policy-gated tools to external hosts.
This admin server exposes local setup operations that can write connector
configuration, store credentials through the daemon, and launch OAuth flows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

SERVER_NAME = "capdep-admin"


_ADMIN_TOOLS: tuple[mcp_types.Tool, ...] = (
    mcp_types.Tool(
        name="setup_status",
        title="Setup status",
        description="Return daemon-owned setup checks and remediation actions.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        annotations=mcp_types.ToolAnnotations(
            title="Setup status",
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    ),
    mcp_types.Tool(
        name="gmail_oauth_status",
        title="Gmail OAuth status",
        description="Return daemon-owned Google Gmail MCP OAuth configuration status.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        annotations=mcp_types.ToolAnnotations(
            title="Gmail OAuth status",
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    ),
    mcp_types.Tool(
        name="gmail_configure_oauth_client",
        title="Configure Gmail OAuth client",
        description=(
            "Store the Google OAuth client ID/secret through the daemon and "
            "write the managed Gmail MCP server config."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "client_id": {
                    "type": "string",
                    "description": "Google OAuth client ID.",
                },
                "client_secret": {
                    "type": "string",
                    "description": "Google OAuth client secret.",
                },
            },
            "required": ["client_id", "client_secret"],
            "additionalProperties": False,
        },
        annotations=mcp_types.ToolAnnotations(
            title="Configure Gmail OAuth client",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    ),
    mcp_types.Tool(
        name="gmail_oauth_login",
        title="Authorize Gmail OAuth",
        description=(
            "Launch the browser OAuth flow through the daemon and store the "
            "Gmail token cache after the user completes Google login."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "open_browser": {
                    "type": "boolean",
                    "description": "Open the default browser automatically.",
                    "default": True,
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Seconds to wait for the OAuth callback.",
                    "minimum": 1,
                    "default": 180,
                },
            },
            "additionalProperties": False,
        },
        annotations=mcp_types.ToolAnnotations(
            title="Authorize Gmail OAuth",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    ),
)


def discover_admin_tools() -> list[mcp_types.Tool]:
    return list(_ADMIN_TOOLS)


async def dispatch_admin_tool(
    client: DaemonClient,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> mcp_types.CallToolResult:
    args = arguments or {}
    try:
        if name == "setup_status":
            result = await client.call("setup.status")
        elif name == "gmail_oauth_status":
            result = await client.call("setup.google_gmail.oauth_status")
        elif name == "gmail_configure_oauth_client":
            result = await client.call(
                "setup.google_gmail.configure_oauth",
                {
                    "client_id": str(args.get("client_id") or ""),
                    "client_secret": str(args.get("client_secret") or ""),
                },
            )
        elif name == "gmail_oauth_login":
            result = await client.call(
                "setup.google_gmail.oauth_login",
                {
                    "open_browser": bool(args.get("open_browser", True)),
                    "timeout_seconds": int(args.get("timeout_seconds") or 180),
                },
            )
        else:
            return _error_result(f"unknown admin tool: {name}")
    except Exception as e:
        return _error_result(str(e))

    return _ok_result(result)


def _ok_result(result: Any) -> mcp_types.CallToolResult:
    structured = result if isinstance(result, dict) else None
    text = json.dumps(result, indent=2) if isinstance(result, dict | list) else str(result)
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=False,
    )


def _error_result(message: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=message)],
        isError=True,
    )


async def build_admin_server(client: DaemonClient) -> Server:
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return discover_admin_tools()

    @server.call_tool()
    async def _call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> mcp_types.CallToolResult:
        return await dispatch_admin_tool(client, name, arguments)

    return server


async def serve_admin(socket_path: Path | None = None) -> None:
    socket = socket_path or default_socket_path()
    client = DaemonClient(socket)
    server = await build_admin_server(client)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
