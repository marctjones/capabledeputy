"""Reusable MCP conformance fixtures for deterministic security tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

from capabledeputy.policy.labels import LabelState
from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.adapter import LabeledMcpAdapter
from capabledeputy.upstream.config import UpstreamServerConfig, UpstreamToolOverride


class InMemoryMcpConformanceHarness:
    """Builds a fake upstream MCP server and registers it through CapDep."""

    def __init__(
        self,
        *,
        server_name: str = "conformance",
        tools: list[mcp_types.Tool] | None = None,
        resources: list[mcp_types.Resource] | None = None,
        resource_text: Mapping[str, str] | None = None,
        tool_outputs: Mapping[str, dict[str, Any]] | None = None,
    ) -> None:
        self.server_name = server_name
        self.tools = tools or []
        self.resources = resources or []
        self.resource_text = dict(resource_text or {})
        self.tool_outputs = dict(tool_outputs or {})
        self.server = self._build_server()

    async def register(
        self,
        *,
        inherent_tags: LabelState | None = None,
        strict: bool = True,
        disabled_tools: set[str] | None = None,
        disabled_kinds: set[str] | None = None,
        overrides: dict[str, UpstreamToolOverride] | None = None,
    ) -> tuple[LabeledMcpAdapter, ToolRegistry]:
        registry = ToolRegistry()
        config = UpstreamServerConfig(
            name=self.server_name,
            command=("noop",),
            inherent_tags=inherent_tags or LabelState(),
            strict=strict,
            disabled_tools=frozenset(disabled_tools or set()),
            disabled_kinds=frozenset(disabled_kinds or set()),
            tool_overrides=overrides or {},
        )
        async with create_connected_server_and_client_session(self.server) as session:
            adapter = LabeledMcpAdapter(config=config, session=session)
            await adapter.register_tools(registry)
        return adapter, registry

    @asynccontextmanager
    async def connected_adapter(
        self,
        *,
        inherent_tags: LabelState | None = None,
    ) -> AsyncIterator[LabeledMcpAdapter]:
        config = UpstreamServerConfig(
            name=self.server_name,
            command=("noop",),
            inherent_tags=inherent_tags or LabelState(),
        )
        async with create_connected_server_and_client_session(self.server) as session:
            yield LabeledMcpAdapter(config=config, session=session)

    def _build_server(self) -> Server:
        server: Server = Server(self.server_name)

        @server.list_tools()
        async def _list_tools() -> list[mcp_types.Tool]:
            return list(self.tools)

        @server.call_tool()
        async def _call_tool(
            name: str,
            arguments: dict[str, Any] | None,
        ) -> mcp_types.CallToolResult:
            structured = self.tool_outputs.get(name, {"name": name, "args": arguments or {}})
            return mcp_types.CallToolResult(
                content=[
                    mcp_types.TextContent(
                        type="text",
                        text=str(structured.get("text", structured)),
                    ),
                ],
                structuredContent=structured,
                isError=False,
            )

        @server.list_resources()
        async def _list_resources() -> list[mcp_types.Resource]:
            return list(self.resources)

        @server.read_resource()
        async def _read_resource(uri: AnyUrl) -> str:
            uri_text = str(uri)
            return self.resource_text.get(
                uri_text,
                self.resource_text.get(uri_text.rstrip("/"), ""),
            )

        return server
