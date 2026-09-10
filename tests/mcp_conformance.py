"""Reusable MCP conformance fixtures for deterministic security tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import anyio
import mcp.types as mcp_types
from mcp import ClientSession
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_client_server_memory_streams

from capabledeputy.policy.labels import LabelState
from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.adapter import LabeledMcpAdapter
from capabledeputy.upstream.config import UpstreamServerConfig, UpstreamToolOverride


@asynccontextmanager
async def create_connected_server_and_client_session(
    server: Server,
) -> AsyncIterator[ClientSession]:
    """mcp 2.x dropped this helper (was `mcp.shared.memory.
    create_connected_server_and_client_session`); rebuild it from the
    lower-level `create_client_server_memory_streams` + a background
    `server.run(...)` task, same shape the old helper provided."""
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                server.run,
                server_read,
                server_write,
                server.create_initialization_options(),
            )
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                yield session
            tg.cancel_scope.cancel()


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
        async def _on_list_tools(
            ctx: ServerRequestContext,
            params: mcp_types.PaginatedRequestParams | None,
        ) -> mcp_types.ListToolsResult:
            return mcp_types.ListToolsResult(tools=list(self.tools))

        async def _on_call_tool(
            ctx: ServerRequestContext,
            params: mcp_types.CallToolRequestParams,
        ) -> mcp_types.CallToolResult:
            structured = self.tool_outputs.get(
                params.name,
                {"name": params.name, "args": params.arguments or {}},
            )
            return mcp_types.CallToolResult(
                content=[
                    mcp_types.TextContent(
                        type="text",
                        text=str(structured.get("text", structured)),
                    ),
                ],
                structured_content=structured,
                is_error=False,
            )

        async def _on_list_resources(
            ctx: ServerRequestContext,
            params: mcp_types.PaginatedRequestParams | None,
        ) -> mcp_types.ListResourcesResult:
            return mcp_types.ListResourcesResult(resources=list(self.resources))

        async def _on_read_resource(
            ctx: ServerRequestContext,
            params: mcp_types.ReadResourceRequestParams,
        ) -> mcp_types.ReadResourceResult:
            uri_text = str(params.uri)
            text = self.resource_text.get(
                uri_text,
                self.resource_text.get(uri_text.rstrip("/"), ""),
            )
            return mcp_types.ReadResourceResult(
                contents=[
                    mcp_types.TextResourceContents(uri=uri_text, text=text),
                ],
            )

        return Server(
            self.server_name,
            on_list_tools=_on_list_tools,
            on_call_tool=_on_call_tool,
            on_list_resources=_on_list_resources,
            on_read_resource=_on_read_resource,
        )
