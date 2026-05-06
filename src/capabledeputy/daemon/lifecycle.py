"""Daemon process lifecycle: start (run forever), stop (via socket), status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.daemon.server import Daemon
from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path


async def run_daemon(socket_path: Path | None = None) -> None:
    daemon = Daemon(socket_path or default_socket_path())
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
