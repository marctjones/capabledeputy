"""Shared MCP-stdio scaffolding for CapableDeputy's bundled servers.

Each server in this package follows the same shape:
  1. A list of tool descriptors (name, description, inputSchema, handler)
  2. A `serve()` coroutine that wires them to MCP stdio

This module factors out the wiring so each server file stays small.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

# A tool handler takes the parsed args dict and returns either:
#   - a string (rendered as TextContent)
#   - a dict (rendered as structuredContent + JSON text fallback)
ToolHandler = Callable[[dict[str, Any]], Awaitable[str | dict[str, Any]]]


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    annotations: dict[str, bool] | None = None


def _to_content_list(result: str | dict[str, Any]) -> list[mcp_types.ContentBlock]:
    """Convert a handler's return value to MCP content blocks.

    Dicts are rendered as structuredContent (when wrapped in CallToolResult)
    and also as a text-fallback so clients without structured-content
    support still see something readable. Strings are TextContent.
    """
    if isinstance(result, str):
        return [mcp_types.TextContent(type="text", text=result)]
    text = json.dumps(result, indent=2, sort_keys=True, default=str)
    return [mcp_types.TextContent(type="text", text=text)]


async def serve_tools(server_name: str, tools: list[ToolDescriptor]) -> None:
    """Wire ``tools`` into an MCP stdio server identified by ``server_name``.

    Blocks until the stdio connection closes (typically when the parent
    MCP host terminates the subprocess).
    """
    server: Server = Server(server_name)
    tool_by_name: dict[str, ToolDescriptor] = {t.name: t for t in tools}

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        out: list[mcp_types.Tool] = []
        for t in tools:
            annotations = None
            if t.annotations is not None:
                annotations = mcp_types.ToolAnnotations(**t.annotations)
            out.append(
                mcp_types.Tool(
                    name=t.name,
                    description=t.description,
                    inputSchema=t.input_schema,
                    annotations=annotations,
                ),
            )
        return out

    @server.call_tool()
    async def _call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> list[mcp_types.ContentBlock]:
        if name not in tool_by_name:
            raise ValueError(f"unknown tool: {name}")
        args = arguments or {}
        result = await tool_by_name[name].handler(args)
        return _to_content_list(result)

    async with stdio_server() as (read, write):
        await server.run(
            read,
            write,
            server.create_initialization_options(),
        )
