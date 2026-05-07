"""MCP stdio server that proxies tool calls to a CapableDeputy daemon.

The daemon must already be running. The server connects via the daemon's
JSON-RPC socket, discovers tools via tool.list, and forwards tool calls
through tool.call. Policy denials surface as tool execution errors
(isError=true) so the calling agent (e.g. Claude Code) sees them and
adapts in its own loop.

The `--session-id` argument binds the server to a specific CapableDeputy
session, so labels accumulate and policy decisions are made against
that session's state across the conversation.

Spec leverage (per modelcontextprotocol.io/specification/2025-11-25):

  - Real inputSchema per tool (not the empty `{"type": "object"}`
    placeholder).
  - structuredContent + text fallback for dict outputs, per
    "Structured Content" §.
  - isError=true on policy denials and tool errors, per "Tool
    Execution Errors" §.
  - ToolAnnotations (readOnlyHint / destructiveHint / openWorldHint)
    derived from the capability kind so MCP hosts can render
    appropriate UI confirmations per spec security guidance.
  - _meta carries CapableDeputy-specific capability metadata so
    capability-aware hosts can do further filtering.
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


_ANNOTATIONS_BY_KIND: dict[str, dict[str, bool]] = {
    "READ_FS": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "WRITE_FS": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    "SEND_EMAIL": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
    "WEB_FETCH": {"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
    "CALENDAR_READ": {"readOnlyHint": True, "idempotentHint": True},
    "CALENDAR_WRITE": {"readOnlyHint": False, "destructiveHint": True},
    "QUEUE_PURCHASE": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
}


def _annotations_for(tool: dict[str, Any]) -> mcp_types.ToolAnnotations | None:
    hints = _ANNOTATIONS_BY_KIND.get(tool["capability_kind"])
    if not hints:
        return None
    return mcp_types.ToolAnnotations(
        title=tool["name"],
        **hints,
    )


def _tool_meta(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "io.capabledeputy/capability_kind": tool["capability_kind"],
        "io.capabledeputy/inherent_labels": tool.get("inherent_labels", []),
    }


async def discover_tools(client: DaemonClient) -> list[mcp_types.Tool]:
    result = await client.call("tool.list")
    tools: list[mcp_types.Tool] = []
    for tool in result["tools"]:
        schema = tool.get("parameters_schema") or {"type": "object"}
        annotations = _annotations_for(tool)
        tools.append(
            mcp_types.Tool(
                name=tool["name"],
                title=tool["name"],
                description=tool["description"],
                inputSchema=schema,
                annotations=annotations,
                **{"_meta": _tool_meta(tool)},
            ),
        )
    return tools


async def dispatch_tool(
    client: DaemonClient,
    session_id: UUID,
    name: str,
    arguments: dict[str, Any],
) -> mcp_types.CallToolResult:
    result = await client.call(
        "tool.call",
        {
            "session_id": str(session_id),
            "tool": name,
            "args": arguments,
        },
    )

    if result.get("error"):
        text = f"tool error: {result['error']}"
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            isError=True,
        )

    if result["decision"] != "allow":
        rule = result.get("rule") or "no_rule"
        reason = result.get("reason") or ""
        text = f"policy denied (decision={result['decision']}, rule={rule}): {reason}"
        meta: dict[str, Any] = {
            "io.capabledeputy/decision": result["decision"],
            "io.capabledeputy/rule": rule,
            "io.capabledeputy/effective_labels": result.get("effective_labels", []),
        }
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            isError=True,
            **{"_meta": meta},
        )

    output = result.get("output") or {}
    structured: dict[str, Any] | None = output if isinstance(output, dict) else None
    text_payload = json.dumps(output, indent=2) if isinstance(output, dict | list) else str(output)
    if result.get("labels_added"):
        text_payload += (
            "\n\n[capdep: session labels expanded with " + ", ".join(result["labels_added"]) + "]"
        )

    call_meta: dict[str, Any] = {
        "io.capabledeputy/labels_added": result.get("labels_added", []),
    }

    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text_payload)],
        structuredContent=structured,
        isError=False,
        **{"_meta": call_meta},
    )


def _to_content(result: mcp_types.CallToolResult) -> list[mcp_types.ContentBlock]:
    return list(result.content)


async def build_server(client: DaemonClient, session_id: UUID) -> Server:
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return await discover_tools(client)

    @server.call_tool()
    async def _call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> mcp_types.CallToolResult:
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
