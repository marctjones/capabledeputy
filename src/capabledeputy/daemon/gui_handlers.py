"""GUI-focused daemon RPC handlers.

These handlers provide stable, read-oriented shapes for native clients. They do
not make policy decisions; they aggregate existing daemon state so GUI clients
do not have to scrape CLI-oriented endpoints.
"""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.audit.events import EventType
from capabledeputy.daemon.handlers import Handler
from capabledeputy.version import __version__


def make_gui_handlers(app: App) -> dict[str, Handler]:
    async def app_status(params: dict[str, Any]) -> dict[str, Any]:
        tools = app.registry.list()
        sessions = list(app.graph._sessions.values())
        pending = app.approval_queue.list(status=None)
        upstream = _upstream_status(app)
        return {
            "version": __version__,
            "daemon": {
                "connected": True,
                "audit_path": _audit_path(app),
                "tool_count": len(tools),
                "session_count": len(sessions),
                "active_session_count": sum(1 for s in sessions if str(s.status) == "active"),
                "pending_approval_count": sum(1 for a in pending if str(a.status) == "pending"),
            },
            "model": {
                "planner": type(app.llm_client).__name__ if app.llm_client is not None else "",
                "quarantined": (
                    type(app.quarantined_llm).__name__ if app.quarantined_llm is not None else ""
                ),
                "local_available": _has_mlx(),
            },
            "upstream_servers": upstream,
            "capabilities_by_kind": _tools_by_kind(tools),
        }

    async def setup_status(params: dict[str, Any]) -> dict[str, Any]:
        upstream = _upstream_status(app)
        relationship_groups = getattr(app.policy_context, "relationship_groups", None)
        relationship_group_count = len(getattr(relationship_groups, "groups", {}) or {})
        return {
            "checks": [
                {
                    "id": "daemon",
                    "title": "Daemon",
                    "status": "ok",
                    "detail": "Connected to CapDep daemon.",
                },
                {
                    "id": "model",
                    "title": "Model backend",
                    "status": "ok" if app.llm_client is not None else "warning",
                    "detail": (
                        type(app.llm_client).__name__
                        if app.llm_client is not None
                        else "No planner LLM client is wired."
                    ),
                },
                {
                    "id": "google-oauth",
                    "title": "Google OAuth / MCP",
                    "status": "ok" if upstream else "warning",
                    "detail": (
                        f"{len(upstream)} upstream server(s) configured."
                        if upstream
                        else "No upstream Google/MCP server status is available."
                    ),
                },
                {
                    "id": "relationship-groups",
                    "title": "Relationship groups",
                    "status": "ok" if relationship_group_count else "warning",
                    "detail": f"{relationship_group_count} group(s) loaded.",
                },
                {
                    "id": "approval-patterns",
                    "title": "Approval patterns",
                    "status": "ok",
                    "detail": f"{len(app.approval_queue.patterns.list())} pattern(s) loaded.",
                },
                {
                    "id": "apple-automation",
                    "title": "Apple Automation / TCC",
                    "status": "manual",
                    "detail": (
                        "macOS grants Automation permissions interactively "
                        "in System Settings."
                    ),
                },
                {
                    "id": "notifications",
                    "title": "Notifications",
                    "status": "manual",
                    "detail": "The native app must request notification permission from macOS.",
                },
            ],
        }

    async def policy_explain(params: dict[str, Any]) -> dict[str, Any]:
        events = await app.audit.read_all()
        session_id = str(params.get("session_id", ""))
        event_id = str(params.get("audit_id", ""))
        candidates = [
            e
            for e in events
            if e.event_type == EventType.POLICY_DECIDED
            and (not session_id or str(e.session_id) == session_id)
            and (not event_id or str(e.audit_id) == event_id)
        ]
        if not candidates:
            return {"found": False, "message": "No matching policy decision found."}
        event = candidates[-1]
        payload = event.payload
        decision = str(payload.get("decision", ""))
        rule = str(payload.get("rule", ""))
        reason = str(payload.get("reason", ""))
        return {
            "found": True,
            "audit_id": str(event.audit_id),
            "session_id": str(event.session_id) if event.session_id else "",
            "decision": decision,
            "rule": rule,
            "reason": reason,
            "plain_english": _plain_policy_explanation(decision, rule, reason),
            "payload": payload,
        }

    async def provenance_graph(params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("session_id", ""))
        events = await app.audit.read_all()
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for event in events:
            if session_id and str(event.session_id) != session_id:
                continue
            payload = event.payload
            if event.event_type == EventType.PROVENANCE_NODE:
                node_id = str(payload.get("node_id", ""))
                if node_id:
                    nodes[node_id] = {
                        "id": node_id,
                        "kind": payload.get("kind", ""),
                        "materialized_id": payload.get("materialized_id", ""),
                        "label_state": payload.get("label_state"),
                        "metadata": payload.get("metadata") or {},
                    }
            elif event.event_type == EventType.PROVENANCE_EDGE:
                edges.append(
                    {
                        "from": payload.get("from_node_id", ""),
                        "to": payload.get("to_node_id", ""),
                        "kind": payload.get("kind", ""),
                        "metadata": payload.get("metadata") or {},
                    },
                )
        return {"nodes": list(nodes.values()), "edges": edges}

    async def macos_frontmost_context(params: dict[str, Any]) -> dict[str, Any]:
        return _frontmost_context()

    return {
        "app.status": app_status,
        "setup.status": setup_status,
        "policy.explain": policy_explain,
        "provenance.graph": provenance_graph,
        "macos.frontmost_context": macos_frontmost_context,
    }


def _tools_by_kind(tools: list[Any]) -> dict[str, int]:
    by_kind: dict[str, int] = {}
    for tool in tools:
        kind = getattr(tool, "capability_kind", "")
        key = getattr(kind, "value", str(kind))
        by_kind[key] = by_kind.get(key, 0) + 1
    return by_kind


def _audit_path(app: App) -> str:
    path = getattr(app.audit, "_path", None) or getattr(app.audit, "path", None)
    return str(path) if path is not None else ""


def _upstream_status(app: App) -> list[dict[str, Any]]:
    manager = getattr(app, "upstream_manager", None)
    server_status = getattr(manager, "server_status", None)
    if not server_status:
        return []
    return [
        {
            "name": status.name,
            "state": status.state,
            "registered_tool_count": status.registered_tool_count,
            "rejected_tool_count": status.rejected_tool_count,
            "error": status.error,
            "transport": status.transport,
            "url": status.url,
        }
        for status in sorted(server_status.values(), key=lambda s: s.name)
    ]


def _has_mlx() -> bool:
    try:
        import mlx.core as mx  # type: ignore[import-not-found]

        return bool(mx.metal.is_available())
    except Exception:
        return False


def _plain_policy_explanation(decision: str, rule: str, reason: str) -> str:
    text = f"{rule} {reason}".lower()
    if "no matching capability" in text:
        return "This session was not granted authority for the requested action."
    if "egress" in text:
        return "This would move data to an external destination or lower-trust place."
    if "untrusted" in text:
        return "This action is influenced by lower-integrity or external-untrusted input."
    if "brewer" in text or "conflict" in text:
        return "This session has conflicting compartments and cannot mix those flows safely."
    if "approval" in text or decision == "require_approval":
        return "This action is allowed only after explicit human approval."
    if decision == "deny":
        return "No safe policy path exists for this action in the current session."
    if decision == "allow":
        return "The daemon policy allowed this action in the current context."
    return reason or "The daemon made a policy decision for this action."


def _frontmost_context() -> dict[str, Any]:
    # Best-effort local macOS context. This is intentionally read-only and uses
    # System Events; macOS may require the user to grant Automation permission.
    import platform
    import subprocess

    if platform.system() != "Darwin":
        return {"available": False, "reason": "not macOS", "chips": []}
    script = (
        'tell application "System Events" to set appName to name of first application process '
        "whose frontmost is true\n"
        "return appName\n"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": str(exc), "chips": []}
    if result.returncode != 0:
        return {
            "available": False,
            "reason": result.stderr.strip() or "osascript failed",
            "chips": [],
        }
    app_name = result.stdout.strip()
    return {
        "available": True,
        "frontmost_app": app_name,
        "chips": [
            {
                "title": "Frontmost app",
                "detail": app_name,
                "kind": "macOS",
                "is_sensitive": False,
                "is_untrusted": False,
            },
        ],
    }
