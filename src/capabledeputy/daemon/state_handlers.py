"""Comprehensive daemon state projections.

This handler family assembles the daemon-owned runtime state that
interactive clients, monitors, and operator dashboards want to query
without stitching together many narrow RPCs.
"""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.labels import legacy_labels_present


def make_state_handlers(app: App) -> dict[str, Handler]:
    async def daemon_state(params: dict[str, Any]) -> dict[str, Any]:
        tools = app.registry.list()
        sessions = list(app.graph.list())
        approvals = app.approval_queue.list(status=None)
        memory_snapshot = app.memory.snapshot()
        workstream_snapshot = await app.workstreams.snapshot()
        onguard_snapshot = await _onguard_snapshot(app)
        coordinator_snapshot = _coordinator_snapshot(app)
        daemon_snapshot = await _daemon_snapshot(app)
        turn_snapshot = app.turns.snapshot()

        return {
            "schema_version": 1,
            "daemon": daemon_snapshot,
            "model": {
                "planner": type(app.llm_client).__name__ if app.llm_client is not None else "",
                "quarantined": (
                    type(app.quarantined_llm).__name__ if app.quarantined_llm is not None else ""
                ),
                "local_available": _has_mlx(),
                "pool": app.model_pool.status() if app.model_pool is not None else {},
            },
            "clients": {
                "daemon_connections": daemon_snapshot["connections"],
                "registered_onguard_clients": len(onguard_snapshot["clients"]),
                "active_onguard_clients": sum(
                    1
                    for client in onguard_snapshot["clients"]
                    if str(client.get("status", "")) == "active"
                ),
                "session_clients": len(sessions),
            },
            "sessions": {
                "count": len(sessions),
                "active_count": sum(1 for session in sessions if str(session.status) == "active"),
                "by_status": _session_counts(sessions),
                "items": [_session_summary(session, coordinator_snapshot) for session in sessions],
            },
            "workflows": {
                "interactive": _interactive_workflows(
                    sessions,
                    coordinator_snapshot,
                    workstream_snapshot,
                ),
                "approvals": _approval_workflows(approvals),
                "onguard": _onguard_workflows(onguard_snapshot),
            },
            "approvals": {
                "pending_count": sum(
                    1 for approval in approvals if approval.status.value == "pending"
                ),
                "items": [approval.to_dict() for approval in approvals],
            },
            "mcp": {
                "upstream_servers": _upstream_servers(app),
            },
            "tools": {
                "count": len(tools),
                "by_kind": _tools_by_kind(tools),
                "items": [_tool_summary(tool) for tool in tools],
            },
            "labels": {
                "sessions": {
                    str(session.id): {
                        "legacy_label_set": legacy_labels_present(session.label_state),
                        "label_state": session.label_state.to_dict(),
                        "axis_d": session.axis_d.to_dict(),
                    }
                    for session in sessions
                },
                "memory": memory_snapshot["labels_by_key"],
            },
            "memory": memory_snapshot,
            "audit": _audit_snapshot(app),
            "coordination": coordinator_snapshot,
            "turns": turn_snapshot,
            "workstreams": workstream_snapshot,
            "onguard": onguard_snapshot,
        }

    return {"daemon.state": daemon_state}


async def _daemon_snapshot(app: App) -> dict[str, Any]:
    import platform
    import sys
    import time as _time

    from capabledeputy.daemon.handlers import _CODE_VERSION, _DAEMON_PID, _DAEMON_STARTED_AT

    uptime_seconds = int(_time.time() - _DAEMON_STARTED_AT)
    daemon_server = getattr(app, "daemon_server", None)
    connections = (
        await daemon_server.snapshot()
        if daemon_server is not None
        else {
            "active_connections": 0,
            "subscribers_by_stream": {},
            "connection_subscriptions": {},
            "subscription_count": 0,
        }
    )
    return {
        **dict(_CODE_VERSION),
        "pid": _DAEMON_PID,
        "uptime_seconds": uptime_seconds,
        "started_at_epoch": int(_DAEMON_STARTED_AT),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "connections": connections,
    }


def _session_summary(session: Any, coordinator_snapshot: dict[str, Any]) -> dict[str, Any]:
    state = coordinator_snapshot["sessions"].get(str(session.id), {})
    return {
        "id": str(session.id),
        "parent": str(session.parent) if session.parent else None,
        "status": session.status.value,
        "owner": session.owner,
        "intent": session.intent,
        "purpose_handle": session.purpose_handle,
        "enforcement_mode": session.enforcement_mode.value,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "turn_count": len(session.history),
        "label_state": session.label_state.to_dict(),
        "legacy_label_set": legacy_labels_present(session.label_state),
        "reference_handle_count": len(session.reference_handles),
        "used_kinds": sorted(k.value for k in session.used_kinds),
        "coordinator": state,
    }


def _session_counts(sessions: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for session in sessions:
        key = str(session.status.value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _interactive_workflows(
    sessions: list[Any],
    coordinator_snapshot: dict[str, Any],
    workstream_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    workflows: list[dict[str, Any]] = []
    for session in sessions:
        coord = coordinator_snapshot["sessions"].get(str(session.id), {})
        workstream = workstream_snapshot["by_session"].get(str(session.id), {})
        if not coord and str(session.status) != "active":
            continue
        workflows.append(
            {
                "session_id": str(session.id),
                "status": session.status.value,
                "has_active_turn": coord.get("has_active_turn", False),
                "pending_input_count": coord.get("pending_input_count", 0),
                "event_count": coord.get("event_count", 0),
                "last_event_type": coord.get("last_event_type"),
                "intent": session.intent,
                "owner": session.owner,
                "workstream_id": workstream.get("id"),
                "workstream_client_id": workstream.get("client_id"),
                "workstream_status": workstream.get("status"),
                "workstream_lease_until": workstream.get("lease_until"),
            },
        )
    return workflows


def _approval_workflows(approvals: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for approval in approvals:
        out.append(
            {
                "approval_id": approval.id,
                "status": approval.status.value,
                "action": approval.action.value,
                "from_session": str(approval.from_session),
                "to_session": str(approval.to_session) if approval.to_session else None,
                "target": approval.target,
                "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            },
        )
    return out


def _onguard_workflows(onguard_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    workflows: list[dict[str, Any]] = []
    for command in onguard_snapshot["commands"]:
        workflows.append(
            {
                "kind": "command",
                "client_id": command["client_id"],
                "id": command["command_id"],
                "status": command["status"],
                "command": command["command"],
                "claimed_by": command["claimed_by"],
                "lease_until": command["lease_until"],
            },
        )
    for schedule in onguard_snapshot["schedules"]:
        workflows.append(
            {
                "kind": "schedule",
                "client_id": schedule["client_id"],
                "id": schedule["schedule_id"],
                "status": schedule["status"],
                "next_run_at": schedule["next_run_at"],
                "last_run_at": schedule["last_run_at"],
            },
        )
    return workflows


async def _onguard_snapshot(app: App) -> dict[str, Any]:
    clients = await app.onguard.list_clients(kind=None)
    configs = await app.onguard.list_configs(client_id=None, status=None)
    commands = await app.onguard.list_commands(client_id=None, status=None)
    events = await app.onguard.list_events(client_id=None, limit=100)
    artifacts = await app.onguard.list_artifacts(client_id=None, status=None)
    schedules = await app.onguard.list_schedules(client_id=None, status=None)
    return {
        "clients": clients,
        "configs": configs,
        "commands": commands,
        "events": events,
        "artifacts": artifacts,
        "schedules": schedules,
    }


def _coordinator_snapshot(app: App) -> dict[str, Any]:
    return app.session_coordinator.snapshot()


def _audit_snapshot(app: App) -> dict[str, Any]:
    audit_path = getattr(app.audit, "_path", None) or getattr(app.audit, "path", None)
    audit_size_bytes = 0
    audit_path_str = ""
    if audit_path is not None:
        from pathlib import Path

        p = Path(audit_path)
        if p.is_file():
            audit_size_bytes = p.stat().st_size
            audit_path_str = str(p)
    return {"path": audit_path_str, "size_bytes": audit_size_bytes}


def _tools_by_kind(tools: list[Any]) -> dict[str, int]:
    by_kind: dict[str, int] = {}
    for tool in tools:
        kind = tool.capability_kind
        kind_str = kind.value if hasattr(kind, "value") else str(kind)
        by_kind[kind_str] = by_kind.get(kind_str, 0) + 1
    return by_kind


def _tool_summary(tool: Any) -> dict[str, Any]:
    descriptor = tool.describe()
    return {
        "name": descriptor.runtime.name,
        "description": descriptor.runtime.description,
        "runtime": {
            "parameters_schema": descriptor.runtime.parameters_schema,
        },
        "policy": {
            "capability_kind": descriptor.policy.capability_kind,
            "target_arg": descriptor.policy.target_arg,
            "target_template": descriptor.policy.target_template,
            "amount_arg": descriptor.policy.amount_arg,
            "effect_class": descriptor.policy.effect_class,
            "tool_provenance": descriptor.policy.tool_provenance,
            "surfaces_destination_id": descriptor.policy.surfaces_destination_id,
            "risk_ids": list(descriptor.policy.risk_ids),
        },
        "flow": {
            "accepts_handles": descriptor.flow.accepts_handles,
            "handle_arg_names": list(descriptor.flow.handle_arg_names),
            "forbid_restricted_source": descriptor.flow.forbid_restricted_source,
            "has_source_label_lookup": descriptor.flow.has_source_label_lookup,
        },
    }


def _upstream_servers(app: App) -> list[dict[str, Any]]:
    mgr = getattr(app, "upstream_manager", None)
    if mgr is None or not hasattr(mgr, "server_status"):
        return []
    servers: list[dict[str, Any]] = []
    for status in sorted(mgr.server_status.values(), key=lambda s: s.name):
        servers.append(
            {
                "name": status.name,
                "state": status.state,
                "registered_at_epoch": status.registered_at_epoch,
                "registered_tool_count": status.registered_tool_count,
                "rejected_tool_count": status.rejected_tool_count,
                "rejected_tool_names": list(status.rejected_tool_names),
                "error": status.error,
                "command": list(status.command),
                "transport": status.transport,
                "url": status.url,
            },
        )
    return servers


def _has_mlx() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("mlx_lm") is not None
    except Exception:
        return False
