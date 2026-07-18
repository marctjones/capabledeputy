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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

from capabledeputy.observability import get_metrics, log_event
from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.adapter import LabeledMcpAdapter
from capabledeputy.upstream.config import UpstreamServerConfig
from capabledeputy.upstream.supervisor import LiveSession


def _stderr_logger(msg: str) -> None:
    print(msg, file=sys.stderr)


@dataclass(frozen=True)
class UpstreamServerStatus:
    """Per-upstream-server runtime status — what /server surfaces.

    Captured at startup. The supervisor's per-call respawn machinery
    keeps the underlying LiveSession alive, so a server's `state`
    here reflects "did it register successfully at daemon start";
    real-time health monitoring is a separate concern (LiveSession
    handles it transparently)."""

    name: str
    state: str  # "registered" | "failed"
    registered_at_epoch: int
    registered_tool_count: int = 0
    rejected_tool_count: int = 0
    rejected_tool_names: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""
    command: tuple[str, ...] = field(default_factory=tuple)
    transport: str = "stdio"
    url: str = ""


class UpstreamManager:
    def __init__(
        self,
        configs: list[UpstreamServerConfig],
        registry: ToolRegistry,
        email_labeler: Any = None,
    ) -> None:
        self._configs = configs
        self._registry = registry
        self._sessions: list[LiveSession] = []
        self._adapters: list[LabeledMcpAdapter] = []
        self._sessions_by_name: dict[str, LiveSession] = {}
        self._adapters_by_name: dict[str, LabeledMcpAdapter] = {}
        # Issue #34 — per-message email labeling. When provided (loaded
        # from configs/email_label_rules.yaml), every upstream read result
        # is run through the labeler; it returns empty for non-email
        # output (no from/subject), so applying it to all servers is a
        # safe, raise-only enrichment on top of each server's inherent-tag
        # floor.
        self._email_labeler = email_labeler
        # Per-server status tracker (operator visibility via /server).
        self._status: dict[str, UpstreamServerStatus] = {}

    def _record_health(self) -> None:
        """#323 — publish upstream-server health as gauges so a failed MCP
        server (its tools silently missing) is observable in `capdep metrics` /
        `capdep doctor` without reading the audit log."""
        total = len(self._status)
        healthy = sum(1 for s in self._status.values() if s.state == "registered")
        m = get_metrics()
        m.set_gauge("upstream.servers_total", total)
        m.set_gauge("upstream.servers_healthy", healthy)

    async def __aenter__(self) -> UpstreamManager:
        for config in self._configs:
            try:
                await self._connect_and_register(config)
                # Status capture happens after _connect_and_register
                # so the adapter's registered_names + rejected_tools
                # lists are populated.
                adapter = self._adapters[-1]
                self._status[config.name] = UpstreamServerStatus(
                    name=config.name,
                    state="registered",
                    registered_at_epoch=int(time.time()),
                    registered_tool_count=len(adapter.registered_names),
                    rejected_tool_count=len(adapter.rejected_tools),
                    rejected_tool_names=tuple(adapter.rejected_tools),
                    command=tuple(config.command),
                    transport=config.transport,
                    url=config.url,
                )
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
                # #323 — structured line so a degraded upstream is machine-
                # observable, not just a human stderr print.
                log_event(
                    "error",
                    "upstream.spawn_failed",
                    server=config.name,
                    transport=config.transport,
                    error=str(e)[:500],
                )
                # Record the failure so /server surfaces it. Truncate
                # the error to keep the RPC payload sane.
                self._status[config.name] = UpstreamServerStatus(
                    name=config.name,
                    state="failed",
                    registered_at_epoch=int(time.time()),
                    error=str(e)[:500],
                    command=tuple(config.command),
                    transport=config.transport,
                    url=config.url,
                )
        self._record_health()
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
        self._sessions_by_name.clear()
        self._adapters_by_name.clear()

    async def _connect_and_register(
        self,
        config: UpstreamServerConfig,
    ) -> None:
        live = LiveSession(config, spawn_logger=_stderr_logger)
        await live.start()
        self._sessions.append(live)
        self._sessions_by_name[config.name] = live
        result_labeler: Callable[[str, dict[str, Any], Any], Any] | None = None
        if self._email_labeler is not None and getattr(self._email_labeler, "rules", ()):
            labeler = self._email_labeler

            def _result_labeler(_name: str, _args: dict[str, Any], output: Any) -> Any:
                return labeler.labels_for_output(output)

            result_labeler = _result_labeler

        adapter = LabeledMcpAdapter(
            config=config,
            session=live,
            result_labeler=result_labeler,
        )
        await adapter.register_tools(self._registry)
        self._adapters.append(adapter)
        self._adapters_by_name[config.name] = adapter

    async def unload_server(self, name: str) -> tuple[str, ...]:
        """Stop one upstream server and remove its registered tool prefix."""
        session = self._sessions_by_name.pop(name, None)
        if session is not None:
            await session.stop()
            if session in self._sessions:
                self._sessions.remove(session)
        adapter = self._adapters_by_name.pop(name, None)
        if adapter is not None and adapter in self._adapters:
            self._adapters.remove(adapter)
        self._status.pop(name, None)
        self._record_health()
        return self._registry.unregister_prefix(f"{name}.")

    async def reload_server(self, config: UpstreamServerConfig) -> UpstreamServerStatus:
        """Reload one upstream config, replacing stale tools fail-closed."""
        await self.unload_server(config.name)
        try:
            await self._connect_and_register(config)
            adapter = self._adapters[-1]
            status = UpstreamServerStatus(
                name=config.name,
                state="registered",
                registered_at_epoch=int(time.time()),
                registered_tool_count=len(adapter.registered_names),
                rejected_tool_count=len(adapter.rejected_tools),
                rejected_tool_names=tuple(adapter.rejected_tools),
                command=tuple(config.command),
                transport=config.transport,
                url=config.url,
            )
        except Exception as e:
            status = UpstreamServerStatus(
                name=config.name,
                state="failed",
                registered_at_epoch=int(time.time()),
                error=str(e)[:500],
                command=tuple(config.command),
                transport=config.transport,
                url=config.url,
            )
        self._status[config.name] = status
        if status.state == "failed":
            log_event(
                "error",
                "upstream.reload_failed",
                server=config.name,
                error=status.error,
            )
        self._record_health()
        return status

    @property
    def adapters(self) -> list[LabeledMcpAdapter]:
        return list(self._adapters)

    @property
    def sessions(self) -> list[LiveSession]:
        return list(self._sessions)

    @property
    def server_status(self) -> dict[str, UpstreamServerStatus]:
        """Per-upstream-server status snapshot for /server display."""
        return dict(self._status)
