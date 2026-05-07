"""Daemon process lifecycle: start (run forever), stop (via socket), status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.daemon.audit_handlers import make_audit_handlers
from capabledeputy.daemon.handlers import default_handlers
from capabledeputy.daemon.memory_handlers import make_memory_handlers
from capabledeputy.daemon.pattern_handlers import make_pattern_handlers
from capabledeputy.daemon.policy_handlers import make_policy_handlers
from capabledeputy.daemon.server import Daemon
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.daemon.tool_handlers import make_tool_handlers
from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.llm.litellm_client import LiteLLMClient


async def run_daemon(
    socket_path: Path | None = None,
    state_db_path: Path | None = None,
    audit_log_path: Path | None = None,
    model: str | None = None,
) -> None:
    import os

    chosen_model = model or os.environ.get("CAPDEP_LLM_MODEL", "claude-haiku-4-5")
    app = App(
        state_db_path=state_db_path,
        audit_log_path=audit_log_path,
        llm_client=LiteLLMClient(model=chosen_model),
    )
    await app.startup()

    handlers = default_handlers()
    handlers.update(make_session_handlers(app.graph))
    handlers.update(make_audit_handlers(app.audit))
    handlers.update(make_policy_handlers())
    handlers.update(make_tool_handlers(app.registry, app.graph, app.tool_client))
    handlers.update(make_agent_handlers(app))
    handlers.update(make_approval_handlers(app))
    handlers.update(make_pattern_handlers(app))
    handlers.update(make_memory_handlers(app))

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
