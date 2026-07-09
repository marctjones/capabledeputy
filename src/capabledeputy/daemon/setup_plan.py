"""Daemon-owned first-run setup plan and readiness checks (v0.34).

Clients render the plan; the daemon remains the authority for what is
missing, what blocks the first useful workflow, and which recovery
actions are valid.
"""

from __future__ import annotations

import os
from typing import Any

from capabledeputy.app import App
from capabledeputy.cli._managed_config import imap_credentials_present, uvx_spawn_command
from capabledeputy.daemon.google_gmail_setup import GOOGLE_GMAIL_SERVER, google_oauth_statuses
from capabledeputy.daemon.settings_store import load_settings
from capabledeputy.daemon.workflow_templates import (
    FIRST_WORKFLOW_TEMPLATE_ID,
    first_workflow_template,
)
from capabledeputy.llm.factory import (
    mlx_metal_available,
    ollama_reachable,
    resolve_planner_model_spec,
)
from capabledeputy.upstream.credential_vault import default_vault_path, load_credential_vault

FIRST_WORKFLOW_ID = FIRST_WORKFLOW_TEMPLATE_ID

_STATUS_ORDER = {"blocking": 0, "warning": 1, "manual": 2, "ok": 3}


def build_setup_checks(app: App) -> list[dict[str, Any]]:
    """Return the same check rows as ``setup.status``."""
    from capabledeputy.daemon.google_gmail_setup import google_oauth_status
    from capabledeputy.daemon.gui_handlers import (
        _gmail_setup_check_status,
        _google_setup_actions,
        _google_setup_check_detail,
        _upstream_status,
    )

    upstream = _upstream_status(app)
    google_statuses = google_oauth_statuses()["services"]
    gmail_status = google_oauth_status(GOOGLE_GMAIL_SERVER)
    relationship_groups = getattr(app.policy_context, "relationship_groups", None)
    relationship_group_count = len(getattr(relationship_groups, "groups", {}) or {})
    settings = load_settings()
    model_spec = resolve_planner_model_spec(prefer_local_mlx=settings.prefer_local_mlx)
    model_detail = (
        f"{type(app.llm_client).__name__} ({model_spec})"
        if app.llm_client is not None
        else "No planner LLM client is wired."
    )
    if app.llm_client is not None and settings.prefer_local_mlx and not mlx_metal_available():
        model_detail += "; MLX Metal unavailable — Ollama fallback may be active."
    elif app.llm_client is not None and ollama_reachable() and model_spec.startswith("ollama/"):
        model_detail += "; using local Ollama planner."
    imap_loaded = any(server.get("name") == "mail" for server in upstream)
    search_check = _search_provider_check(upstream)
    upstream_check = _configured_upstreams_check(upstream)
    if imap_loaded and imap_credentials_present():
        imap_status = "ok"
        imap_detail = "IMAP mail upstream is loaded with saved credentials."
    elif imap_credentials_present():
        imap_status = "manual"
        imap_detail = (
            "IMAP credentials exist but the mail upstream is not loaded. "
            "Run `capdep imap-setup --register-only`, then restart the daemon."
        )
    else:
        imap_status = "warning"
        imap_detail = (
            "No IMAP credentials. Run `capdep imap-setup` with a Gmail App Password "
            "to read mail without Google MCP preview approval."
        )
    return [
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
            "detail": model_detail,
            "blocking": app.llm_client is None,
        },
        {
            "id": "policy",
            "title": "Policy configuration",
            "status": "ok" if app.policy_context is not None else "blocking",
            "detail": (
                "Policy context loaded from operator configs."
                if app.policy_context is not None
                else "Policy context failed to load — daemon may be misconfigured."
            ),
            "blocking": app.policy_context is None,
        },
        upstream_check,
        {
            "id": "google-oauth",
            "title": "Google OAuth / MCP",
            "status": _gmail_setup_check_status(gmail_status, upstream),
            "detail": _google_setup_check_detail(google_statuses, upstream),
            "actions": _google_setup_actions(google_statuses),
        },
        {
            "id": "imap-email",
            "title": "IMAP email",
            "status": imap_status,
            "detail": imap_detail,
            "actions": [
                {
                    "id": "setup.imap_setup",
                    "label": "Run IMAP Setup",
                    "kind": "client_cli",
                    "command": "capdep imap-setup",
                },
            ],
        },
        search_check,
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
            "detail": "macOS grants Automation permissions interactively in System Settings.",
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
            "detail": "Run config.validate for exact daemon config and manifest diagnostics.",
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
    ]


def _step_status(check: dict[str, Any]) -> str:
    if check.get("blocking"):
        return "blocking"
    status = str(check.get("status") or "manual")
    if status in {"warning", "blocking"}:
        return status
    return status if status in _STATUS_ORDER else "manual"


def build_setup_steps(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordered onboarding steps derived from setup checks."""
    onboarding_order = [
        "daemon",
        "model",
        "policy",
        "config-validation",
        "configured-mcp",
        "google-oauth",
        "imap-email",
        "web-search",
        "relationship-groups",
        "source-bindings",
        "approval-patterns",
        "apple-automation",
        "notifications",
        "daemon-settings",
    ]
    by_id = {check["id"]: check for check in checks}
    steps: list[dict[str, Any]] = []
    for order, step_id in enumerate(onboarding_order, start=1):
        check = by_id.get(step_id)
        if check is None:
            continue
        status = _step_status(check)
        steps.append(
            {
                "id": step_id,
                "order": order,
                "title": check.get("title", step_id),
                "status": status,
                "blocking": status == "blocking",
                "detail": check.get("detail", ""),
                "actions": list(check.get("actions") or []),
            },
        )
    return steps


def _workflow_blockers(steps: list[dict[str, Any]]) -> list[str]:
    return [step["id"] for step in steps if step.get("blocking")]


def build_setup_plan(app: App) -> dict[str, Any]:
    checks = build_setup_checks(app)
    steps = build_setup_steps(checks)
    summary = {
        "blocking": sum(1 for step in steps if step["status"] == "blocking"),
        "warning": sum(1 for step in steps if step["status"] == "warning"),
        "manual": sum(1 for step in steps if step["status"] == "manual"),
        "ok": sum(1 for step in steps if step["status"] == "ok"),
    }
    blockers = _workflow_blockers(steps)
    workflow_ready = not blockers and app.llm_client is not None
    ready = summary["blocking"] == 0
    first_workflow = first_workflow_template()
    return {
        "ready": ready,
        "workflow_ready": workflow_ready,
        "first_workflow": {
            "id": first_workflow["id"],
            "title": first_workflow["title"],
            "purpose_handle": first_workflow["purpose_handle"],
            "ready": workflow_ready,
            "blockers": blockers,
            "hint": (
                f"Run {first_workflow['title'].lower()} in the "
                f"{first_workflow['purpose_handle']} purpose with native tools."
                if workflow_ready
                else "Resolve blocking setup steps before running the first workflow."
            ),
        },
        "summary": summary,
        "steps": steps,
        "checks": checks,
    }


def _search_provider_check(upstream: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {str(server.get("name")): server for server in upstream}
    bundled = by_name.get("bundled-search")
    kagi = by_name.get("kagi")
    brave_key = bool(os.environ.get("BRAVE_SEARCH_API_KEY", "").strip())
    try:
        vault_has_kagi_key = bool(load_credential_vault(default_vault_path()).env_for("kagi"))
    except Exception:
        vault_has_kagi_key = False
    kagi_key = bool(os.environ.get("KAGI_API_KEY", "").strip()) or vault_has_kagi_key
    uvx_available = uvx_spawn_command()[0] != "uvx"
    actions = [
        {
            "id": "config.validate",
            "label": "Validate Search Config",
            "kind": "daemon_rpc",
        },
    ]
    if kagi and kagi.get("state") == "registered":
        return {
            "id": "web-search",
            "title": "Web search provider",
            "status": "ok",
            "detail": "Kagi search is registered for broad web/news search.",
            "actions": actions,
        }
    if brave_key and bundled and bundled.get("state") == "registered":
        return {
            "id": "web-search",
            "title": "Web search provider",
            "status": "ok",
            "detail": "Bundled search is registered with Brave Search API coverage.",
            "actions": actions,
        }
    if kagi_key and not uvx_available:
        detail = "KAGI_API_KEY is set, but uvx is missing so the Kagi MCP cannot launch."
    elif kagi and kagi.get("state") == "failed":
        detail = f"Kagi search failed to launch: {kagi.get('error') or 'unknown error'}"
    elif bundled and bundled.get("state") == "registered":
        detail = (
            "Bundled search is registered, but no Brave or Kagi key is active. "
            "Current-events searches use DuckDuckGo Instant Answer fallback, which is limited."
        )
    else:
        detail = "No full web/news search provider is registered."
    return {
        "id": "web-search",
        "title": "Web search provider",
        "status": "warning",
        "detail": detail,
        "actions": actions,
    }


def _configured_upstreams_check(upstream: list[dict[str, Any]]) -> dict[str, Any]:
    actions = [
        {
            "id": "config.validate",
            "label": "Validate MCP Config",
            "kind": "daemon_rpc",
        },
        {
            "id": "config.log_locations",
            "label": "Open Logs",
            "kind": "daemon_rpc",
        },
    ]
    if not upstream:
        return {
            "id": "configured-mcp",
            "title": "Configured MCP servers",
            "status": "manual",
            "detail": "No upstream MCP server status is available from the daemon.",
            "actions": actions,
        }

    failed = [server for server in upstream if str(server.get("state") or "") == "failed"]
    if failed:
        summarized = []
        for server in failed[:4]:
            name = str(server.get("name") or "unknown")
            error = str(server.get("error") or "unknown error").strip()
            summarized.append(f"{name}: {error}")
        suffix = "" if len(failed) <= 4 else f"; plus {len(failed) - 4} more"
        return {
            "id": "configured-mcp",
            "title": "Configured MCP servers",
            "status": "warning",
            "detail": (
                "Configured MCP server(s) failed to launch: " + "; ".join(summarized) + suffix
            ),
            "actions": actions,
            "failed_servers": [str(server.get("name") or "") for server in failed],
        }

    registered = [server for server in upstream if str(server.get("state") or "") == "registered"]
    rejected_tools = sum(int(server.get("rejected_tool_count") or 0) for server in upstream)
    detail = f"{len(registered)}/{len(upstream)} configured MCP server(s) registered."
    if rejected_tools:
        detail += f" {rejected_tools} tool(s) were rejected by policy classification."
    return {
        "id": "configured-mcp",
        "title": "Configured MCP servers",
        "status": "ok",
        "detail": detail,
        "actions": actions,
    }


def build_setup_check(app: App) -> dict[str, Any]:
    plan = build_setup_plan(app)
    blocking_steps = [step for step in plan["steps"] if step.get("blocking")]
    return {
        "ok": plan["ready"],
        "ready": plan["ready"],
        "workflow_ready": plan["workflow_ready"],
        "first_workflow": plan["first_workflow"]["id"],
        "blocking_steps": blocking_steps,
        "summary": plan["summary"],
    }
