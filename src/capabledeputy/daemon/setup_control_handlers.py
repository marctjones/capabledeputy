"""Daemon-owned setup, runtime-control, and source-binding RPCs.

These handlers keep practical desktop setup behind the same daemon boundary as
policy and approval state. GUI clients may render buttons and native panels,
but durable state changes and policy-relevant previews stay here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from capabledeputy.app import App
from capabledeputy.audit.events import Event, EventType
from capabledeputy.cli._managed_config import user_config_dir
from capabledeputy.cli.setup_domains import OFFICE_AUTOMATION_APPS
from capabledeputy.daemon.handlers import Handler
from capabledeputy.daemon.settings_store import update_settings
from capabledeputy.policy.bindings import (
    BindingError,
    BindingSet,
    SourceLocationLabelBinding,
    WriteDiscipline,
    canonicalize,
)
from capabledeputy.policy.bindings import (
    load as load_bindings,
)
from capabledeputy.policy.reversibility import MutabilityLabel, ReversibilityLabel
from capabledeputy.policy.tiers import Tier

_GOOGLE_READ_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    },
)
_GOOGLE_WRITE_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file",
    },
)


def make_setup_control_handlers(
    app: App,
    *,
    source_bindings_path: Path | None = None,
) -> dict[str, Handler]:
    bindings_path = source_bindings_path or user_config_dir() / "source_bindings.yaml"

    async def setup_run_action(params: dict[str, Any]) -> dict[str, Any]:
        action_id = str(params.get("action_id") or params.get("id") or "")
        if action_id == "config.validate":
            return _native_action(
                "config.validate",
                "Run config.validate from the client and display the structured result.",
                directive="validate_config",
            )
        if action_id == "config.log_locations":
            return _native_action(
                "config.log_locations",
                "Run config.log_locations and open the returned path in the client.",
                directive="show_log_locations",
            )
        if action_id == "source_binding.list":
            return {
                "action_id": action_id,
                "client_directive": "show_section",
                "kind": "client_navigation",
                "section": "trust",
                "enabled": True,
            }
        if action_id in {
            "macos.automation_settings",
            "apple_automation.open_settings",
        }:
            return {
                "action_id": action_id,
                "client_directive": "open_url",
                "kind": "open_url",
                "url": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
                "label": "Open macOS Automation Privacy Settings",
                "enabled": True,
                "audit_required": False,
            }
        raise ValueError(f"unknown setup action: {action_id}")

    async def connector_status(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "connectors": [
                _google_workspace_connector(),
                *[_office_connector(app_info) for app_info in OFFICE_AUTOMATION_APPS],
            ],
        }

    async def runtime_status(params: dict[str, Any]) -> dict[str, Any]:
        return {"runtime": _runtime_state(app)}

    async def runtime_set_automation_paused(params: dict[str, Any]) -> dict[str, Any]:
        paused = bool(params.get("paused"))
        state = _runtime_state(app)
        state["automation_paused"] = paused
        _set_runtime_state(app, state)
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={"action": "runtime.automation_paused", "paused": paused},
            ),
        )
        return {"runtime": state}

    async def runtime_request_screen_control(params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("session_id") or "")
        reason = str(params.get("reason") or "CapDepMac requested generic screen control")
        settings, _changed = update_settings({"enable_screen_control": True})
        state = _runtime_state(app)
        state["screen_control_requested"] = True
        state["screen_control_session_id"] = session_id
        _set_runtime_state(app, state)
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "runtime.screen_control.request",
                    "session_id": session_id,
                    "reason": reason,
                },
            ),
        )
        return {
            "runtime": state,
            "settings": settings.to_dict(),
            "message": (
                "Generic screen control is enabled in daemon settings. macOS TCC "
                "permission still requires explicit user approval outside CapDep."
            ),
        }

    async def source_binding_list(params: dict[str, Any]) -> dict[str, Any]:
        bindings = _load_binding_set(bindings_path)
        return {
            "path": str(bindings_path),
            "bindings": [_binding_to_dict(binding) for binding in bindings.bindings],
        }

    async def source_binding_preview(params: dict[str, Any]) -> dict[str, Any]:
        uri = str(params.get("uri") or "")
        bindings = _load_binding_set(bindings_path)
        try:
            resolution = bindings.resolve(uri)
        except BindingError as exc:
            return {
                "ok": False,
                "uri": uri,
                "canonical_uri": _safe_canonicalize(uri),
                "error": str(exc),
                "matched_bindings": [],
            }
        return {
            "ok": True,
            "uri": uri,
            "canonical_uri": resolution.canonical_destination_id,
            "category": resolution.category,
            "tier": resolution.tier.value,
            "write_discipline": resolution.write_discipline.value,
            "risk_ids": list(resolution.risk_ids),
            "matched_bindings": [
                _binding_to_dict(binding) for binding in resolution.matched_bindings
            ],
        }

    async def source_binding_upsert(params: dict[str, Any]) -> dict[str, Any]:
        payload = dict(params.get("binding") or params)
        binding = _binding_from_payload(payload)
        bindings = list(_load_binding_set(bindings_path).bindings)
        replaced = False
        for index, existing in enumerate(bindings):
            if existing.name == binding.name:
                bindings[index] = binding
                replaced = True
                break
        if not replaced:
            bindings.append(binding)
        _write_binding_set(bindings_path, BindingSet(bindings=tuple(bindings)))
        _refresh_runtime_bindings(app, BindingSet(bindings=tuple(bindings)))
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "source_binding.upsert",
                    "name": binding.name,
                    "replaced": replaced,
                    "path": str(bindings_path),
                },
            ),
        )
        return {
            "path": str(bindings_path),
            "binding": _binding_to_dict(binding),
            "replaced": replaced,
        }

    async def source_binding_delete(params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        bindings = list(_load_binding_set(bindings_path).bindings)
        kept = [binding for binding in bindings if binding.name != name]
        if len(kept) == len(bindings):
            raise ValueError(f"source binding not found: {name}")
        binding_set = BindingSet(bindings=tuple(kept))
        _write_binding_set(bindings_path, binding_set)
        _refresh_runtime_bindings(app, binding_set)
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "source_binding.delete",
                    "name": name,
                    "path": str(bindings_path),
                },
            ),
        )
        return {"path": str(bindings_path), "deleted": name}

    return {
        "connector.status": connector_status,
        "runtime.status": runtime_status,
        "runtime.automation_pause": runtime_set_automation_paused,
        "runtime.screen_control.request": runtime_request_screen_control,
        "setup.run_action": setup_run_action,
        "source_binding.list": source_binding_list,
        "source_binding.preview": source_binding_preview,
        "source_binding.upsert": source_binding_upsert,
        "source_binding.delete": source_binding_delete,
    }


def _runtime_state(app: App) -> dict[str, Any]:
    state = getattr(app, "_runtime_controls", None)
    if not isinstance(state, dict):
        state = {
            "automation_paused": False,
            "screen_control_requested": False,
            "screen_control_session_id": "",
        }
        _set_runtime_state(app, state)
    return dict(state)


def _set_runtime_state(app: App, state: dict[str, Any]) -> None:
    app._runtime_controls = dict(state)  # type: ignore[attr-defined]


def _native_action(method: str, detail: str, *, directive: str) -> dict[str, Any]:
    # #422 — `client_directive` is the typed instruction the client executes
    # (open a window, validate config, …). Clients must branch on this, not on
    # the daemon `method` string (kept for back-compat / audit). The daemon owns
    # what the client should do; the client just renders it.
    return {
        "kind": "client_action",
        "client_directive": directive,
        "method": method,
        "detail": detail,
        "enabled": True,
    }


def _office_connector(app_info: dict[str, str]) -> dict[str, Any]:
    return {
        "id": app_info["id"],
        "name": app_info["name"],
        "type": "local_app",
        "bundle_id": app_info["bundle_id"],
        "status": "permission_needed",
        "detail": (
            "Use bounded app-specific automation tools; macOS grants Automation "
            "permission on first use."
        ),
        "actions": [_open_macos_automation_action()],
    }


def _google_workspace_connector(
    *,
    runner: Any = subprocess.run,
    executable: str | None = None,
) -> dict[str, Any]:
    """Return a redacted, daemon-owned Google Workspace credential posture.

    The local ``gws`` CLI owns its encrypted OAuth store.  It is deliberately
    queried only through its read-only ``auth status`` command: a GUI or CLI
    receives a posture, never a token, account identifier, or credential path.
    A usable token with broad write scopes is *not* reported ready for the
    daily read-only workflow; provider permission is distinct from CapDep
    authority, and least privilege remains an operator-visible prerequisite.
    """
    command = executable or shutil.which("gws")
    if not command:
        return {
            "id": "google-workspace",
            "name": "Google Workspace",
            "type": "local_oauth_provider",
            "status": "unavailable",
            "detail": "Google Workspace CLI (gws) is not installed.",
            "actions": [],
        }
    try:
        completed = runner(
            [command, "auth", "status"],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
        payload = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        payload = {}
    if not payload:
        return {
            "id": "google-workspace",
            "name": "Google Workspace",
            "type": "local_oauth_provider",
            "status": "not_connected",
            "detail": "Google Workspace credentials are not ready in the daemon-owned gws store.",
            "actions": [],
        }

    scopes = frozenset(str(scope) for scope in payload.get("scopes", ()))
    missing = sorted(_GOOGLE_READ_SCOPES - scopes)
    write_scopes = sorted(_GOOGLE_WRITE_SCOPES & scopes)
    token_valid = payload.get("token_valid") is True
    if not token_valid:
        status = "reauthentication_needed"
        detail = (
            "Google Workspace credentials need reauthentication before read-only workflows can run."
        )
    elif missing or write_scopes:
        status = "least_privilege_reauth_needed"
        detail = (
            "Google Workspace token is usable but not least-privilege for CapDep's "
            "read-only workflow; reauthenticate with the exact Gmail, Calendar, "
            "and Drive read scopes."
        )
    else:
        status = "ready_read_only"
        detail = (
            "Google Workspace read-only credentials are ready; CapDep still gates "
            "every tool call by capability and labels."
        )
    return {
        "id": "google-workspace",
        "name": "Google Workspace",
        "type": "local_oauth_provider",
        "status": status,
        "detail": detail,
        "read_scopes_present": sorted(_GOOGLE_READ_SCOPES & scopes),
        "missing_read_scopes": missing,
        "write_scopes_present": write_scopes,
        "actions": [],
    }


def _open_macos_automation_action() -> dict[str, Any]:
    return {
        "id": "macos.automation_settings",
        "label": "Open macOS Automation Settings",
        "kind": "open_url",
        "enabled": True,
    }


def _load_binding_set(path: Path) -> BindingSet:
    if not path.exists():
        return BindingSet(bindings=())
    return load_bindings(path)


def _binding_to_dict(binding: SourceLocationLabelBinding) -> dict[str, Any]:
    return {
        "name": binding.name,
        "scope_pattern_canonical": binding.scope_pattern_canonical,
        "category": binding.category,
        "default_tier": binding.default_tier.value,
        "write_discipline": binding.write_discipline.value,
        "risk_ids": list(binding.risk_ids),
        "assignment_provenance": binding.assignment_provenance,
        "reversibility": (
            binding.reversibility.to_dict() if binding.reversibility is not None else None
        ),
        "mutability": binding.mutability.to_dict() if binding.mutability is not None else None,
    }


def _binding_from_payload(payload: dict[str, Any]) -> SourceLocationLabelBinding:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("source binding name is required")
    scope = str(payload.get("scope_pattern_canonical") or "").strip()
    if not scope:
        raise ValueError("scope_pattern_canonical is required")
    _validate_scope(scope)
    category = str(payload.get("category") or "").strip()
    if not category:
        raise ValueError("category is required")
    tier = Tier(str(payload.get("default_tier") or "personal"))
    wd = WriteDiscipline(str(payload.get("write_discipline") or "in-place"))
    rev_raw = payload.get("reversibility")
    mut_raw = payload.get("mutability")
    return SourceLocationLabelBinding(
        name=name,
        scope_pattern_canonical=scope,
        category=category,
        default_tier=tier,
        reversibility=ReversibilityLabel.from_dict(rev_raw) if isinstance(rev_raw, dict) else None,
        mutability=MutabilityLabel.from_dict(mut_raw) if isinstance(mut_raw, dict) else None,
        write_discipline=wd,
        risk_ids=tuple(str(risk) for risk in (payload.get("risk_ids") or [])),
        assignment_provenance=str(payload.get("assignment_provenance") or "operator-declared"),
    )


def _validate_scope(scope: str) -> None:
    sample = scope.replace("**", "sample").replace("*", "sample")
    canonicalize(sample)
    literal_count = sum(1 for ch in scope if ch not in "*?")
    if literal_count < 10:
        raise ValueError("source binding scope is too broad")


def _write_binding_set(path: Path, binding_set: BindingSet) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"bindings": [_binding_to_dict(binding) for binding in binding_set.bindings]}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    path.chmod(0o600)


def _refresh_runtime_bindings(app: App, binding_set: BindingSet) -> None:
    if app.policy_context is not None:
        app.policy_context = replace(
            app.policy_context,
            bindings=binding_set if binding_set.bindings else None,
        )
        app.tool_client.update_policy_context(app.policy_context)


def _safe_canonicalize(uri: str) -> str:
    try:
        return canonicalize(uri)
    except BindingError:
        return ""
