"""Daemon process lifecycle: start (run forever), stop (via socket), status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.daemon.audit_handlers import make_audit_handlers
from capabledeputy.daemon.bundle_handlers import make_bundle_handlers
from capabledeputy.daemon.demo_handlers import make_demo_handlers
from capabledeputy.daemon.extract_handlers import make_extract_handlers
from capabledeputy.daemon.handlers import default_handlers
from capabledeputy.daemon.memory_handlers import make_memory_handlers
from capabledeputy.daemon.pattern_handlers import make_pattern_handlers
from capabledeputy.daemon.policy_handlers import make_policy_handlers
from capabledeputy.daemon.programmatic_handlers import make_programmatic_handlers
from capabledeputy.daemon.server import Daemon
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.daemon.tool_handlers import make_tool_handlers
from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.llm.litellm_client import LiteLLMClient
from capabledeputy.policy.capabilities import DEFAULT_MAX_DELEGATION_DEPTH
from capabledeputy.secrets import load_anthropic_api_key
from capabledeputy.upstream.config import load_config_file
from capabledeputy.upstream.manager import UpstreamManager


def max_delegation_depth() -> int:
    """Configured max delegation chain depth (002 FR-006). Deterministic,
    operator-set: `CAPDEP_MAX_DELEGATION_DEPTH` env, else the default.
    A non-positive / unparseable value falls back to the default
    (fail-safe: never an unbounded chain)."""
    import os

    raw = os.environ.get("CAPDEP_MAX_DELEGATION_DEPTH")
    if raw is None:
        return DEFAULT_MAX_DELEGATION_DEPTH
    try:
        v = int(raw)
    except ValueError:
        return DEFAULT_MAX_DELEGATION_DEPTH
    return v if v > 0 else DEFAULT_MAX_DELEGATION_DEPTH


def _resolve_daemon_config(config_path: Path | None) -> Path | None:
    """Daemon config file resolution (opt-in, fail-soft).

    Precedence: explicit arg > CAPDEP_CONFIG env. Returns the path only
    if it exists; loading external MCP servers is a deliberate operator
    action, never implicit, so an absent/unset config is silently no-op.
    """
    import os

    raw = config_path or (
        Path(os.environ["CAPDEP_CONFIG"]) if os.environ.get("CAPDEP_CONFIG") else None
    )
    if raw is None or not raw.is_file():
        return None
    return raw


def _report_admission(manager: UpstreamManager) -> None:
    """Surface the strict-mode admission outcome to the daemon log so
    the operator can see which upstream tools were registered and which
    were refused (the WI-1 fail-closed rejections)."""
    import sys

    for adapter in manager.adapters:
        rejected = adapter.rejected_tools
        line = f"[upstream] {adapter.name}: registered ok"
        if rejected:
            line += f"; REFUSED {len(rejected)} unclassified: {sorted(rejected)}"
        print(line, file=sys.stderr)


async def run_daemon(
    socket_path: Path | None = None,
    state_db_path: Path | None = None,
    audit_log_path: Path | None = None,
    model: str | None = None,
    verbose: bool = False,
    policy_preview: bool | None = None,
    config_path: Path | None = None,
) -> None:
    import os

    # Populate ANTHROPIC_API_KEY from CLAUDEAPI.KEY in the cwd if it isn't
    # already set, so users don't need to re-export the env var each shell.
    load_anthropic_api_key()

    # Precedence: explicit arg (CLI flag) > CAPDEP_POLICY_PREVIEW env >
    # default on. The env var is off only for explicit falsey values.
    if policy_preview is None:
        env = os.environ.get("CAPDEP_POLICY_PREVIEW")
        enable_policy_preview = True
        if env is not None and env.strip().lower() in ("0", "false", "no", "off"):
            enable_policy_preview = False
    else:
        enable_policy_preview = policy_preview

    chosen_model = model or os.environ.get("CAPDEP_LLM_MODEL", "claude-haiku-4-5")
    quarantined_model = os.environ.get("CAPDEP_QUARANTINED_LLM_MODEL")
    quarantined_client = None
    if quarantined_model:
        quarantined_client = LiteLLMClient(model=quarantined_model)

    skills_env = os.environ.get("CAPDEP_SKILLS_DIR")
    skills_dir = Path(skills_env) if skills_env else None

    app = App(
        state_db_path=state_db_path,
        audit_log_path=audit_log_path,
        llm_client=LiteLLMClient(model=chosen_model),
        quarantined_llm=quarantined_client,
        skills_dir=skills_dir,
        enable_policy_preview=enable_policy_preview,
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
    handlers.update(make_programmatic_handlers(app))
    handlers.update(make_bundle_handlers(app))
    handlers.update(make_demo_handlers(app))
    handlers.update(make_extract_handlers(app))

    daemon = Daemon(
        socket_path or default_socket_path(),
        handlers=handlers,
        verbose=verbose,
    )

    async def _relay_audit(event) -> None:
        await daemon.publish("audit", event.to_dict())

    app.audit.subscribe(_relay_audit)

    # Opt-in: if a daemon config file with an `upstream_servers:` section
    # is provided, spawn those MCP servers for the daemon's lifetime and
    # register their (policy-gated, labeled) tools into the SAME
    # app.registry every client uses — so capdep chat / console / tui see
    # them automatically, already gated. Absent config -> native only.
    resolved = _resolve_daemon_config(config_path)
    upstream_configs = load_config_file(resolved) if resolved is not None else []

    if upstream_configs:
        async with UpstreamManager(upstream_configs, app.registry) as manager:
            _report_admission(manager)
            await daemon.serve()
    else:
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
