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
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

SERVER_NAME = "capdep-admin"

_ADMIN_META: dict[str, Any] = {
    "io.capabledeputy/surface": "admin",
    "io.capabledeputy/authority": "local_setup",
    "io.capabledeputy/session_bound": False,
}

_GENERIC_OBJECT_OUTPUT: dict[str, Any] = {"type": "object", "additionalProperties": True}


_ADMIN_TOOLS: tuple[mcp_types.Tool, ...] = (
    mcp_types.Tool(
        name="setup_status",
        title="Setup status",
        description="Return daemon-owned setup checks and remediation actions.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema=_GENERIC_OBJECT_OUTPUT,
        annotations=mcp_types.ToolAnnotations(
            title="Setup status",
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        **{"_meta": _ADMIN_META},  # pyright: ignore[reportArgumentType]
    ),
)


def discover_admin_tools() -> list[mcp_types.Tool]:
    return list(_ADMIN_TOOLS)


async def dispatch_admin_tool(
    client: DaemonClient,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> mcp_types.CallToolResult:
    try:
        if name == "setup_status":
            result = await client.call("setup.status")
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
        structured_content=structured,
        is_error=False,
        **{"_meta": _ADMIN_META},  # pyright: ignore[reportArgumentType]
    )


def _error_result(message: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=message)],
        is_error=True,
    )


async def build_admin_server(client: DaemonClient) -> Server:
    async def _on_list_tools(
        ctx: ServerRequestContext,
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(tools=discover_admin_tools())

    async def _on_call_tool(
        ctx: ServerRequestContext,
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        return await dispatch_admin_tool(client, params.name, params.arguments)

    server: Server = Server(
        SERVER_NAME,
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )
    return server


async def serve_admin(socket_path: Path | None = None) -> None:
    socket = socket_path or default_socket_path()
    # Untrusted: this stdio server is spawned by an MCP host (any agent,
    # not proven-operator) exactly like the session-bound server — see
    # daemon/authz.py.
    client = DaemonClient(socket, trusted=False)
    server = await build_admin_server(client)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
