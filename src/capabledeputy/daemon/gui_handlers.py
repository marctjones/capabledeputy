"""GUI-focused daemon RPC handlers.

These handlers provide stable, read-oriented shapes for native clients. They do
not make policy decisions; they aggregate existing daemon state so GUI clients
do not have to scrape CLI-oriented endpoints.
"""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction, ApprovalStatus
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.google_gmail_setup import (
    GOOGLE_GMAIL_SERVER,
    GOOGLE_OAUTH_SERVICES,
    configure_gmail_oauth_client,
    configure_google_oauth_client,
    gmail_oauth_status,
    google_oauth_status,
    google_oauth_statuses,
    redacted_gmail_oauth_payload,
    redacted_google_oauth_payload,
    revoke_google_oauth_token,
    run_gmail_oauth_login,
    run_google_oauth_login,
)
from capabledeputy.daemon.handlers import Handler
from capabledeputy.daemon.settings_store import load_settings
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
        google_statuses = google_oauth_statuses()["services"]
        gmail_status = google_oauth_status(GOOGLE_GMAIL_SERVER)
        relationship_groups = getattr(app.policy_context, "relationship_groups", None)
        relationship_group_count = len(getattr(relationship_groups, "groups", {}) or {})
        settings = load_settings()
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
                    "status": _gmail_setup_check_status(gmail_status, upstream),
                    "detail": _google_setup_check_detail(google_statuses, upstream),
                    "actions": _google_setup_actions(google_statuses),
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
                        "macOS grants Automation permissions interactively in System Settings."
                    ),
                    "actions": [
                        {
                            "id": "macos.automation_settings",
                            "label": "Open Settings",
                            "kind": "open_url",
                        },
                    ],
                },
                {
                    "id": "notifications",
                    "title": "Notifications",
                    "status": "manual" if settings.notifications_enabled else "warning",
                    "detail": (
                        "The native app must request notification permission from macOS."
                        if settings.notifications_enabled
                        else "Notifications are disabled in daemon-owned settings."
                    ),
                },
                {
                    "id": "daemon-settings",
                    "title": "Daemon-owned settings",
                    "status": "ok",
                    "detail": "Client preferences are loaded from the daemon settings store.",
                },
                {
                    "id": "source-bindings",
                    "title": "Source bindings",
                    "status": (
                        "ok"
                        if getattr(app.policy_context, "bindings", None) is not None
                        else "manual"
                    ),
                    "detail": "Operator-curated source labels are edited through daemon RPCs.",
                    "actions": [
                        {
                            "id": "source_binding.list",
                            "label": "Open Bindings",
                            "kind": "client_navigation",
                        },
                    ],
                },
                {
                    "id": "config-validation",
                    "title": "Configuration validation",
                    "status": "manual",
                    "detail": (
                        "Run config.validate for exact daemon config and manifest diagnostics."
                    ),
                    "actions": [
                        {
                            "id": "config.validate",
                            "label": "Validate Configuration",
                            "kind": "daemon_rpc",
                        },
                        {
                            "id": "config.log_locations",
                            "label": "Open Logs",
                            "kind": "daemon_rpc",
                        },
                    ],
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

    async def approval_detail(params: dict[str, Any]) -> dict[str, Any]:
        approval = app.approval_queue.get(int(params["id"]))
        siblings = (
            app.approval_queue.siblings(approval.sibling_group_id)
            if approval.sibling_group_id is not None
            else []
        )
        pending_siblings = [s for s in siblings if s.status == ApprovalStatus.PENDING]
        rule = approval.rule or ""
        reason = approval.justification
        return {
            "approval": approval.to_dict(),
            "effect_text": _approval_effect_text(approval.action),
            "plain_policy_reason": _plain_policy_explanation(
                "require_approval",
                rule,
                reason,
            ),
            "source_summary": {
                "labels_in": approval.labels_in.to_dict(),
                "labels_out": approval.labels_out.to_dict(),
                "capability_requested": (
                    approval.capability_requested.to_dict()
                    if approval.capability_requested is not None
                    else None
                ),
            },
            "sibling_group": {
                "id": str(approval.sibling_group_id) if approval.sibling_group_id else "",
                "pending_count": len(pending_siblings),
                "approvable": len(pending_siblings) > 1,
            },
            "suggested_actions": _approval_suggested_actions(approval.action),
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

    async def google_gmail_oauth_status(params: dict[str, Any]) -> dict[str, Any]:
        return gmail_oauth_status()

    async def google_oauth_status_handler(params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("service_id") or "")
        if service_id:
            return google_oauth_status(service_id)
        return google_oauth_statuses()

    async def google_configure_oauth(params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("service_id") or GOOGLE_GMAIL_SERVER)
        status = configure_google_oauth_client(
            service_id,
            client_id=str(params.get("client_id") or ""),
            client_secret=str(params.get("client_secret") or ""),
        )
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "google.configure_oauth",
                    "service_id": service_id,
                    "status": redacted_google_oauth_payload(status),
                },
            ),
        )
        return status

    async def google_oauth_login(params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("service_id") or GOOGLE_GMAIL_SERVER)
        status = await run_google_oauth_login(
            service_id,
            open_browser=bool(params.get("open_browser", True)),
            timeout_seconds=int(params.get("timeout_seconds") or 180),
        )
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "google.oauth_login",
                    "service_id": service_id,
                    "status": redacted_google_oauth_payload(status),
                },
            ),
        )
        return status

    async def google_oauth_revoke(params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("service_id") or GOOGLE_GMAIL_SERVER)
        status = revoke_google_oauth_token(service_id)
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "google.oauth_revoke",
                    "service_id": service_id,
                    "status": redacted_google_oauth_payload(status),
                },
            ),
        )
        return status

    async def google_gmail_configure_oauth(params: dict[str, Any]) -> dict[str, Any]:
        status = configure_gmail_oauth_client(
            client_id=str(params.get("client_id") or ""),
            client_secret=str(params.get("client_secret") or ""),
        )
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "google_gmail.configure_oauth",
                    "status": redacted_gmail_oauth_payload(status),
                },
            ),
        )
        return status

    async def google_gmail_oauth_login(params: dict[str, Any]) -> dict[str, Any]:
        status = await run_gmail_oauth_login(
            open_browser=bool(params.get("open_browser", True)),
            timeout_seconds=int(params.get("timeout_seconds") or 180),
        )
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "google_gmail.oauth_login",
                    "status": redacted_gmail_oauth_payload(status),
                },
            ),
        )
        return status

    return {
        "app.status": app_status,
        "setup.status": setup_status,
        "setup.google.oauth_status": google_oauth_status_handler,
        "setup.google.configure_oauth": google_configure_oauth,
        "setup.google.oauth_login": google_oauth_login,
        "setup.google.oauth_revoke": google_oauth_revoke,
        "setup.google_gmail.oauth_status": google_gmail_oauth_status,
        "setup.google_gmail.configure_oauth": google_gmail_configure_oauth,
        "setup.google_gmail.oauth_login": google_gmail_oauth_login,
        "policy.explain": policy_explain,
        "approval.detail": approval_detail,
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


def _gmail_setup_check_status(
    gmail_status: dict[str, Any],
    upstream: list[dict[str, Any]],
) -> str:
    if any(server.get("name") == "google-gmail" for server in upstream) and gmail_status.get(
        "token_configured",
    ):
        return "ok"
    if gmail_status.get("client_id_configured") and gmail_status.get("client_secret_configured"):
        return "manual"
    return "warning"


def _gmail_setup_check_detail(
    gmail_status: dict[str, Any],
    upstream: list[dict[str, Any]],
) -> str:
    running = any(server.get("name") == "google-gmail" for server in upstream)
    if running and gmail_status.get("token_configured"):
        return "Gmail MCP is configured, authorized, and loaded by the daemon."
    if gmail_status.get("token_configured"):
        return "Gmail OAuth token is configured. Restart the daemon to load Gmail MCP."
    if gmail_status.get("client_id_configured") and gmail_status.get("client_secret_configured"):
        return "Gmail OAuth client is saved. Authorize Gmail to create the token cache."
    if gmail_status.get("configured"):
        return "Gmail MCP server config exists, but OAuth client files are incomplete."
    return "Gmail MCP OAuth is not configured."


def _google_setup_check_detail(
    statuses: list[dict[str, Any]],
    upstream: list[dict[str, Any]],
) -> str:
    configured = sum(1 for status in statuses if status.get("configured"))
    authorized = sum(1 for status in statuses if status.get("token_configured"))
    loaded = sum(
        1
        for status in statuses
        if status.get("server") in {server.get("name") for server in upstream}
    )
    if configured == authorized == loaded == len(statuses):
        return "Google Workspace MCP connectors are configured, authorized, and loaded."
    return (
        f"{configured}/{len(statuses)} configured, "
        f"{authorized}/{len(statuses)} authorized, {loaded}/{len(statuses)} loaded."
    )


def _google_setup_actions(statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for status in statuses:
        service_id = str(status.get("service_id") or status.get("server") or "")
        display_name = str(status.get("display_name") or service_id)
        actions.append(
            {
                "id": f"setup.google.{service_id}.configure_oauth",
                "label": f"Configure {display_name} OAuth",
                "kind": "daemon_form",
                "enabled": service_id in GOOGLE_OAUTH_SERVICES,
            },
        )
        actions.append(
            {
                "id": f"setup.google.{service_id}.oauth_login",
                "label": f"Authorize {display_name}",
                "kind": "daemon_browser_oauth",
                "enabled": (
                    bool(status.get("client_id_configured"))
                    and bool(status.get("client_secret_configured"))
                ),
            },
        )
    return actions


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


def _approval_effect_text(action: ApprovalAction) -> str:
    if action == ApprovalAction.SEND_EMAIL:
        return "Send an email using the exact approved payload and destination."
    if action == ApprovalAction.QUEUE_PURCHASE:
        return "Queue a purchase using the exact approved item/vendor payload."
    if action == ApprovalAction.EXECUTE_DESTRUCTIVE:
        return "Execute a destructive operation with a one-shot approved capability."
    if action == ApprovalAction.DECLASSIFY:
        return "Treat the reviewed payload as explicitly approved for release."
    if action == ApprovalAction.GRANT:
        return "Grant scoped authority only as represented by the approval payload."
    if action == ApprovalAction.MERGE:
        return "Merge session state only after this explicit approval."
    return "Apply the daemon-defined approval action after explicit operator consent."


def _approval_suggested_actions(action: ApprovalAction) -> list[dict[str, str]]:
    base = [
        {
            "id": "deny",
            "title": "Deny",
            "detail": "Do not allow this action.",
        },
        {
            "id": "defer",
            "title": "Defer",
            "detail": "Leave this request pending for later review.",
        },
    ]
    if action == ApprovalAction.SEND_EMAIL:
        base.extend(
            [
                {
                    "id": "draft-only",
                    "title": "Draft Only",
                    "detail": "Prefer a draft workflow when direct send is not necessary.",
                },
                {
                    "id": "add-relationship",
                    "title": "Add Relationship",
                    "detail": (
                        "Remember this recipient only if they are a trusted recurring counterparty."
                    ),
                },
                {
                    "id": "narrow-pattern",
                    "title": "Create Narrow Pattern",
                    "detail": "Allow only this action/target pattern for a short period.",
                },
            ],
        )
    else:
        base.append(
            {
                "id": "narrow-pattern",
                "title": "Create Narrow Pattern",
                "detail": "Allow only this action/target pattern for a short period.",
            },
        )
    return base


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
