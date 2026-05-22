"""Subprocess-based stdio MCP client lifecycle with crash recovery.

Each upstream MCP server is wrapped in a `LiveSession` supervisor
that handles spawn / respawn / backoff transparently. The adapter
holds the `LiveSession` (not a raw `ClientSession`); calls flow
through the supervisor so a dead subprocess respawns and the call
retries automatically.

Use as an async context manager:

    async with UpstreamManager(configs, registry) as manager:
        ...  # all upstream tools registered in `registry`
    # subprocesses cleaned up on exit
"""

from __future__ import annotations

import sys
from types import TracebackType

from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.adapter import LabeledMcpAdapter
from capabledeputy.upstream.config import UpstreamServerConfig
from capabledeputy.upstream.supervisor import LiveSession


def _stderr_logger(msg: str) -> None:
    print(msg, file=sys.stderr)


class UpstreamManager:
    def __init__(
        self,
        configs: list[UpstreamServerConfig],
        registry: ToolRegistry,
    ) -> None:
        self._configs = configs
        self._registry = registry
        self._sessions: list[LiveSession] = []
        self._adapters: list[LabeledMcpAdapter] = []

    async def __aenter__(self) -> UpstreamManager:
        for config in self._configs:
            try:
                await self._connect_and_register(config)
            except Exception as e:
                # One bad upstream must not nuke the daemon. Log loudly
                # and continue with the rest — the operator can fix the
                # config and the supervisor will resume on next call.
                print(
                    f"[upstream] FAILED to spawn {config.name!r} on startup: {e} — "
                    "this upstream's tools will not be registered. Restart the "
                    "daemon after fixing the config.",
                    file=sys.stderr,
                )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Tear down in reverse order so spawned-second is killed-first.
        for session in reversed(self._sessions):
            await session.stop()
        self._sessions.clear()

    async def _connect_and_register(
        self,
        config: UpstreamServerConfig,
    ) -> None:
        live = LiveSession(config, spawn_logger=_stderr_logger)
        await live.start()
        self._sessions.append(live)
        adapter = LabeledMcpAdapter(config=config, session=live)
        await adapter.register_tools(self._registry)
        self._adapters.append(adapter)

    @property
    def adapters(self) -> list[LabeledMcpAdapter]:
        return list(self._adapters)

    @property
    def sessions(self) -> list[LiveSession]:
        return list(self._sessions)
