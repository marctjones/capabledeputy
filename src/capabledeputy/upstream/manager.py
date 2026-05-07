"""Subprocess-based stdio MCP client lifecycle.

Spawns upstream MCP servers as subprocesses, opens stdio sessions to
them, and hands the resulting `ClientSession` to a `LabeledMcpAdapter`
for tool registration.

Use as an async context manager:

    async with UpstreamManager(configs, registry) as manager:
        ...  # all upstream tools registered in `registry`
    # subprocesses cleaned up on exit
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from types import TracebackType

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.adapter import LabeledMcpAdapter
from capabledeputy.upstream.config import UpstreamServerConfig


class UpstreamManager:
    def __init__(
        self,
        configs: list[UpstreamServerConfig],
        registry: ToolRegistry,
    ) -> None:
        self._configs = configs
        self._registry = registry
        self._stack: AsyncExitStack | None = None
        self._adapters: list[LabeledMcpAdapter] = []

    async def __aenter__(self) -> UpstreamManager:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        for config in self._configs:
            await self._connect_and_register(config)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(exc_type, exc_val, exc_tb)
            self._stack = None

    async def _connect_and_register(
        self,
        config: UpstreamServerConfig,
    ) -> None:
        if self._stack is None:
            raise RuntimeError("UpstreamManager not entered")
        # `effective_command` returns the bare command if no isolation
        # is configured, or the podman/docker-wrapped argv if it is.
        # MCP transport stays stdio either way.
        cmd = config.effective_command()
        params = StdioServerParameters(
            command=cmd[0],
            args=list(cmd[1:]),
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(self._registry)
        self._adapters.append(adapter)

    @property
    def adapters(self) -> list[LabeledMcpAdapter]:
        return list(self._adapters)
