"""Daemon RPC handlers for operator settings and config diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.app import App
from capabledeputy.audit.events import Event, EventType
from capabledeputy.cli._managed_config import (
    resolve_daemon_config_with_source,
    user_config_dir,
    user_default_daemon_config_path,
)
from capabledeputy.config.manifest import RuntimeManifest
from capabledeputy.daemon.handlers import Handler
from capabledeputy.daemon.settings_store import (
    default_settings_path,
    load_settings,
    update_settings,
)
from capabledeputy.upstream.config import load_config_file


def make_settings_handlers(
    app: App,
    *,
    config_path: Path | None = None,
    runtime_manifest: RuntimeManifest | None = None,
) -> dict[str, Handler]:
    async def settings_get(params: dict[str, Any]) -> dict[str, Any]:
        settings = load_settings()
        return {
            "settings": settings.to_dict(),
            "path": str(default_settings_path()),
        }

    async def settings_update(params: dict[str, Any]) -> dict[str, Any]:
        updates = params.get("settings", params)
        if not isinstance(updates, dict):
            raise ValueError("settings update must be an object")
        settings, changed = update_settings(updates)
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "settings.update",
                    "changed": list(changed),
                    "settings": settings.to_dict(),
                },
            ),
        )
        return {
            "settings": settings.to_dict(),
            "path": str(default_settings_path()),
            "changed": list(changed),
        }

    async def config_validate(params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("config_path")
        if requested:
            resolved, source = resolve_daemon_config_with_source(str(requested))
        elif config_path is not None and config_path.is_file():
            resolved = config_path
            source = "daemon-start"
        else:
            resolved, source = resolve_daemon_config_with_source(None)
        issues: list[dict[str, str]] = []
        upstream_count = 0
        if resolved is not None:
            try:
                upstream_count = len(load_config_file(resolved))
            except Exception as exc:
                issues.append(
                    {
                        "severity": "error",
                        "subject": str(resolved),
                        "message": str(exc),
                    },
                )
        elif source in {"explicit", "env"}:
            issues.append(
                {
                    "severity": "error",
                    "subject": str(requested or ""),
                    "message": "configured daemon config path does not exist",
                },
            )
        manifest = runtime_manifest or RuntimeManifest.from_runtime(
            registry=app.registry,
            policy_context=app.policy_context,
            upstream_servers=(),
        )
        summary = manifest.summary()
        for issue in summary["errors"]:
            issues.append({"severity": "error", **issue})
        for issue in summary["warnings"]:
            issues.append({"severity": "warning", **issue})
        return {
            "ok": not any(issue["severity"] == "error" for issue in issues),
            "config_path": str(resolved) if resolved is not None else "",
            "config_source": source,
            "user_default_config_path": str(user_default_daemon_config_path()),
            "upstream_server_count": upstream_count or summary["upstream_servers"],
            "tool_count": summary["tools"],
            "hooks": summary["hooks"],
            "issues": issues,
        }

    async def config_log_locations(params: dict[str, Any]) -> dict[str, Any]:
        audit_path = getattr(app.audit, "_path", None) or getattr(app.audit, "path", None)
        return {
            "logs": [
                {
                    "id": "audit",
                    "title": "Audit log",
                    "path": str(audit_path) if audit_path is not None else "",
                },
                {
                    "id": "gui-daemon",
                    "title": "CapDepMac daemon stdout/stderr",
                    "path": "/tmp/capdep-gui-daemon.log",
                },
            ],
            "directories": [
                {
                    "id": "user-config",
                    "title": "User config",
                    "path": str(user_config_dir()),
                },
            ],
        }

    return {
        "settings.get": settings_get,
        "settings.update": settings_update,
        "config.validate": config_validate,
        "config.log_locations": config_log_locations,
    }
