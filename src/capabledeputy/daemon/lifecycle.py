"""Daemon process lifecycle: start (run forever), stop (via socket), status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.audit_handlers import make_audit_handlers
from capabledeputy.daemon.handlers import default_handlers
from capabledeputy.daemon.policy_handlers import make_policy_handlers
from capabledeputy.daemon.server import Daemon
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path


async def run_daemon(
    socket_path: Path | None = None,
    state_db_path: Path | None = None,
    audit_log_path: Path | None = None,
) -> None:
    app = App(state_db_path=state_db_path, audit_log_path=audit_log_path)
    await app.startup()

    handlers = default_handlers()
    handlers.update(make_session_handlers(app.graph))
    handlers.update(make_audit_handlers(app.audit))
    handlers.update(make_policy_handlers())

    daemon = Daemon(socket_path or default_socket_path(), handlers=handlers)
    await daemon.serve()


async def stop_daemon(socket_path: Path | None = None) -> bool:
    client = DaemonClient(socket_path or default_socket_path())
    try:
        await client.call("shutdown")
        return True
    except DaemonNotRunningError:
        return False


async def daemon_status(socket_path: Path | None = None) -> dict[str, Any]:
    client = DaemonClient(socket_path or default_socket_path())
    try:
        result = await client.call("ping")
    except DaemonNotRunningError:
        return {"running": False}
    return {"running": True, "ping": result}
