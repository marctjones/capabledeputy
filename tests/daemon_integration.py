"""Shared live-daemon fixtures for client integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.daemon.audit_handlers import make_audit_handlers
from capabledeputy.daemon.bundle_handlers import make_bundle_handlers
from capabledeputy.daemon.demo_handlers import make_demo_handlers
from capabledeputy.daemon.devbox_handlers import make_devbox_handlers
from capabledeputy.daemon.extract_handlers import make_extract_handlers
from capabledeputy.daemon.gui_handlers import make_gui_handlers
from capabledeputy.daemon.handlers import default_handlers, make_info_handler
from capabledeputy.daemon.lifecycle import build_policy_context_from_configs
from capabledeputy.daemon.mcp_admission_handlers import make_mcp_admission_handlers
from capabledeputy.daemon.memory_handlers import make_memory_handlers
from capabledeputy.daemon.onguard_handlers import make_onguard_handlers
from capabledeputy.daemon.pattern_handlers import make_pattern_handlers
from capabledeputy.daemon.policy_handlers import make_policy_handlers
from capabledeputy.daemon.programmatic_handlers import make_programmatic_handlers
from capabledeputy.daemon.relationship_handlers import make_relationship_handlers
from capabledeputy.daemon.security_context_handlers import make_security_context_handlers
from capabledeputy.daemon.server import Daemon
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.daemon.settings_handlers import make_settings_handlers
from capabledeputy.daemon.setup_control_handlers import make_setup_control_handlers
from capabledeputy.daemon.state_handlers import make_state_handlers
from capabledeputy.daemon.tool_handlers import make_tool_handlers
from capabledeputy.daemon.workstream_handlers import make_workstream_handlers
from capabledeputy.ipc.client import DaemonClient
from tests._socket_helpers import short_socket_path


@dataclass(frozen=True)
class DaemonTestPaths:
    socket: Path
    state_db: Path
    audit_log: Path
    config: Path
    source_bindings: Path


@dataclass(frozen=True)
class RunningDaemon:
    app: App
    client: DaemonClient
    paths: DaemonTestPaths


def daemon_test_paths(tmp_path: Path) -> DaemonTestPaths:
    return DaemonTestPaths(
        socket=short_socket_path(),
        state_db=tmp_path / "state.db",
        audit_log=tmp_path / "audit.jsonl",
        config=tmp_path / "pre_app.yaml",
        source_bindings=tmp_path / "source_bindings.yaml",
    )


def build_test_handlers(app: App, paths: DaemonTestPaths) -> dict[str, Any]:
    handlers = default_handlers()
    handlers["daemon.info"] = make_info_handler(app)
    handlers.update(make_session_handlers(app.graph, app.session_coordinator, app.workstreams))
    handlers.update(make_devbox_handlers(app))
    handlers.update(make_relationship_handlers(app))
    handlers.update(make_security_context_handlers(app))
    handlers.update(make_audit_handlers(app.audit))
    handlers.update(make_policy_handlers())
    handlers.update(make_tool_handlers(app.registry, app.graph, app.tool_client))
    handlers.update(make_agent_handlers(app))
    handlers.update(make_approval_handlers(app))
    handlers.update(make_pattern_handlers(app))
    handlers.update(make_memory_handlers(app))
    handlers.update(make_state_handlers(app))
    handlers.update(make_workstream_handlers(app))
    handlers.update(make_programmatic_handlers(app))
    handlers.update(make_bundle_handlers(app))
    from capabledeputy.daemon.artifact_handlers import make_artifact_handlers
    from capabledeputy.daemon.source_context_handlers import make_source_context_handlers

    handlers.update(make_source_context_handlers())
    handlers.update(make_artifact_handlers())
    handlers.update(make_mcp_admission_handlers(app))
    handlers.update(make_gui_handlers(app))
    handlers.update(make_onguard_handlers(app))
    handlers.update(make_setup_control_handlers(app, source_bindings_path=paths.source_bindings))
    handlers.update(make_settings_handlers(app, config_path=paths.config))
    handlers.update(make_demo_handlers(app))
    handlers.update(make_extract_handlers(app))
    return handlers


async def build_test_daemon(paths: DaemonTestPaths) -> tuple[Daemon, App]:
    policy_context, purposes = build_policy_context_from_configs(
        state_db_path=paths.state_db,
    )
    app = App(
        state_db_path=paths.state_db,
        audit_log_path=paths.audit_log,
        policy_context=policy_context,
        purposes=purposes,
        enable_policy_preview=False,
    )
    await app.startup()
    daemon = Daemon(paths.socket, handlers=build_test_handlers(app, paths))
    app.daemon_server = daemon
    return daemon, app


async def wait_for_socket(path: Path, timeout: float = 2.0) -> None:
    deadline = anyio.current_time() + timeout
    while anyio.current_time() < deadline:
        if path.exists():
            try:
                stream = await anyio.connect_unix(str(path))
                await stream.aclose()
                return
            except (FileNotFoundError, ConnectionRefusedError):
                pass
        await anyio.sleep(0.01)
    raise TimeoutError(f"socket {path} did not become available within {timeout}s")


@asynccontextmanager
async def running_daemon(tmp_path: Path) -> AsyncIterator[RunningDaemon]:
    paths = daemon_test_paths(tmp_path)
    daemon, app = await build_test_daemon(paths)
    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await wait_for_socket(paths.socket)
        client = DaemonClient(paths.socket)
        try:
            yield RunningDaemon(app=app, client=client, paths=paths)
        finally:
            await client.call("shutdown")
