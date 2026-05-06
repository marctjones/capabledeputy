"""MCP stdio server that proxies tool calls to a CapableDeputy daemon.

The daemon must already be running. The server connects via the daemon's
JSON-RPC socket, discovers tools via tool.list, and forwards tool calls
through tool.call. Policy denials and tool errors are surfaced as MCP
text responses so the calling agent (e.g. Claude Code) sees them and
can react in its loop.

The `--session-id` argument binds the server to a specific CapableDeputy
session, so labels accumulate and policy decisions are made against
that session's state across the conversation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

SERVER_NAME = "capdep"


async def discover_tools(client: DaemonClient) -> list[mcp_types.Tool]:
    result = await client.call("tool.list")
    return [
        mcp_types.Tool(
            name=tool["name"],
            description=tool["description"],
            inputSchema={"type": "object"},
        )
        for tool in result["tools"]
    ]


def _format_output(output: Any) -> str:
    if isinstance(output, dict | list):
        return json.dumps(output, indent=2)
    return str(output)


async def dispatch_tool(
    client: DaemonClient,
    session_id: UUID,
    name: str,
    arguments: dict[str, Any],
) -> list[mcp_types.TextContent]:
    result = await client.call(
        "tool.call",
        {
            "session_id": str(session_id),
            "tool": name,
            "args": arguments,
        },
    )

    if result.get("error"):
        return [
            mcp_types.TextContent(
                type="text",
                text=f"tool error: {result['error']}",
            ),
        ]

    if result["decision"] != "allow":
        rule = result.get("rule") or "no_rule"
        reason = result.get("reason") or ""
        return [
            mcp_types.TextContent(
                type="text",
                text=f"policy denied (decision={result['decision']}, rule={rule}): {reason}",
            ),
        ]

    text = _format_output(result.get("output"))
    if result.get("labels_added"):
        text += (
            "\n\n[capdep: session labels expanded with " + ", ".join(result["labels_added"]) + "]"
        )
    return [mcp_types.TextContent(type="text", text=text)]


async def build_server(client: DaemonClient, session_id: UUID) -> Server:
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return await discover_tools(client)

    @server.call_tool()
    async def _call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> list[mcp_types.TextContent]:
        return await dispatch_tool(client, session_id, name, arguments or {})

    return server


async def serve(session_id: UUID, socket_path: Path | None = None) -> None:
    client = DaemonClient(socket_path or default_socket_path())
    server = await build_server(client, session_id)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
