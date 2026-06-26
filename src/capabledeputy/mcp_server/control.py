"""MCP control client for driving the CapableDeputy daemon.

This server is a client surface for external MCP hosts such as Codex or
Claude. It does not implement policy decisions itself; every operation forwards
to a daemon RPC so the daemon remains the source of truth for policy, approval,
provenance, memory, and audit behavior.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.mcp_server.media_results import build_mcp_result

SERVER_NAME = "capdep-control"

_CONTROL_META: dict[str, Any] = {
    "io.capabledeputy/surface": "control",
    "io.capabledeputy/authority": "daemon_control",
    "io.capabledeputy/session_bound": False,
}

_GENERIC_OBJECT_OUTPUT: dict[str, Any] = {"type": "object", "additionalProperties": True}
_EMPTY_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ControlToolSpec:
    name: str
    title: str
    description: str
    rpc: str
    input_schema: dict[str, Any]
    annotations: mcp_types.ToolAnnotations


def _annotations(
    title: str,
    *,
    read_only: bool,
    idempotent: bool,
    destructive: bool = False,
    open_world: bool = False,
) -> mcp_types.ToolAnnotations:
    return mcp_types.ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def _tool(spec: ControlToolSpec) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=spec.name,
        title=spec.title,
        description=spec.description,
        inputSchema=spec.input_schema,
        outputSchema=_GENERIC_OBJECT_OUTPUT,
        annotations=spec.annotations,
        **{"_meta": _CONTROL_META},  # pyright: ignore[reportArgumentType]
    )


def _optional_string_properties(*names: str) -> dict[str, Any]:
    return {name: {"type": "string"} for name in names}


def _schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    additional: bool = False,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": additional,
    }
    if required:
        schema["required"] = required
    return schema


_SESSION_ID_SCHEMA = _schema(
    {"session_id": {"type": "string", "description": "CapDep session ID."}},
    required=["session_id"],
)

_APPROVAL_ID_SCHEMA = _schema(
    {
        "id": {"type": "integer", "description": "Daemon approval ID."},
        "decided_by": {
            "type": "string",
            "description": "Actor recorded as the approval decision maker.",
            "default": "mcp-control",
        },
        "strong_auth": {
            "type": "string",
            "description": "Daemon-recognized strong-auth marker for high-risk approvals.",
        },
    },
    required=["id"],
)

_GENERIC_ARGS_SCHEMA = _schema(
    {"args": {"type": "object", "additionalProperties": True}},
)

_SESSION_MESSAGE_SCHEMA = _schema(
    {
        "session_id": {"type": "string"},
        "message": {"type": "string"},
        "mode": {"type": "string"},
        "max_iterations": {"type": "integer", "minimum": 1},
        "client_id": {"type": "string"},
        "workstream_id": {"type": "string"},
        "lease_token": {"type": "string"},
        "lease_seconds": {"type": "integer", "minimum": 1},
    },
    required=["session_id", "message"],
)
_TURN_ID_SCHEMA = _schema({"turn_id": {"type": "string"}}, required=["turn_id"])
_TURN_EVENTS_SCHEMA = _schema(
    {
        "turn_id": {"type": "string"},
        "after": {"type": "integer", "minimum": 0},
    },
    required=["turn_id"],
)
_TURN_ACK_SCHEMA = _schema(
    {
        "turn_id": {"type": "string"},
        "client_id": {"type": "string"},
    },
    required=["turn_id", "client_id"],
)
_TURN_CANCEL_SCHEMA = _schema(
    {
        "turn_id": {"type": "string"},
        "reason": {"type": "string"},
        "client_id": {"type": "string"},
        "admin_override": {"type": "boolean"},
    },
    required=["turn_id"],
)

_WORKSTREAM_ID_SCHEMA = _schema({"workstream_id": {"type": "string"}}, required=["workstream_id"])

_WORKSTREAM_SESSION_SCHEMA = _schema(
    {
        "session_id": {"type": "string"},
        "client_id": {"type": "string"},
        "lease_seconds": {"type": "integer", "minimum": 1},
        "lease_token": {"type": "string"},
        "reason": {"type": "string"},
        "workstream_id": {"type": "string"},
        "auto_claim": {"type": "boolean"},
        "admin_override": {"type": "boolean"},
    },
    required=["session_id"],
)

_GRANT_ID_SCHEMA = _schema({"grant_id": {"type": "string"}}, required=["grant_id"])

_GOOGLE_SERVICE_ID = {
    "type": "string",
    "enum": ["google-gmail", "google-calendar", "google-drive"],
    "description": "Managed Google Workspace MCP service ID.",
}

_ONGUARD_CLIENT_ID = {"type": "string", "description": "Registered onguard client ID."}
_ONGUARD_COMMAND_ID = {"type": "string", "description": "Daemon onguard command ID."}
_ONGUARD_SCHEDULE_ID = {"type": "string", "description": "Daemon onguard schedule ID."}
_ONGUARD_ARTIFACT_ID = {"type": "string", "description": "Daemon onguard artifact ID."}
_LABELS_SCHEMA = {"type": "array", "items": {"type": "string"}}
_JSON_OBJECT_SCHEMA = {"type": "object", "additionalProperties": True}

_RELATIONSHIP_MEMBER_SCHEMA = _schema(
    {
        "group_id": {"type": "string"},
        "principal_id": {"type": "string"},
    },
    required=["group_id", "principal_id"],
)

_CONTROL_TOOL_SPECS: tuple[ControlToolSpec, ...] = (
    ControlToolSpec(
        "capdep_ping",
        "Ping daemon",
        "Check whether the CapDep daemon responds.",
        "ping",
        _EMPTY_INPUT,
        _annotations("Ping daemon", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "capdep_version",
        "CapDep version",
        "Return the daemon-reported CapDep version.",
        "version",
        _EMPTY_INPUT,
        _annotations("CapDep version", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "daemon_info",
        "Daemon info",
        "Return daemon runtime information.",
        "daemon.info",
        _EMPTY_INPUT,
        _annotations("Daemon info", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "daemon_state",
        "Daemon state",
        "Return the daemon-owned comprehensive runtime snapshot.",
        "daemon.state",
        _EMPTY_INPUT,
        _annotations("Daemon state", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "app_status",
        "App status",
        "Return daemon-owned status for desktop and control clients.",
        "app.status",
        _EMPTY_INPUT,
        _annotations("App status", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "setup_status",
        "Setup status",
        "Return setup checks and remediation actions.",
        "setup.status",
        _EMPTY_INPUT,
        _annotations("Setup status", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "setup_plan",
        "Setup plan",
        "Return ordered onboarding steps and first-workflow readiness.",
        "setup.plan",
        _EMPTY_INPUT,
        _annotations("Setup plan", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "setup_check",
        "Setup check",
        "Return compact readiness gate for CI and first-run smoke tests.",
        "setup.check",
        _EMPTY_INPUT,
        _annotations("Setup check", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "workflow_templates",
        "Workflow templates",
        "Return daemon-owned workflow template catalog for all clients.",
        "workflow.templates",
        _EMPTY_INPUT,
        _annotations("Workflow templates", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "setup_run_action",
        "Run setup action",
        "Resolve a daemon-owned setup remediation action descriptor.",
        "setup.run_action",
        _schema({"action_id": {"type": "string"}}, required=["action_id"]),
        _annotations("Run setup action", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "connector_status",
        "Connector status",
        "Return daemon-owned connector/account setup status.",
        "connector.status",
        _EMPTY_INPUT,
        _annotations("Connector status", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "runtime_status",
        "Runtime controls status",
        "Return daemon-owned runtime control state.",
        "runtime.status",
        _EMPTY_INPUT,
        _annotations("Runtime controls status", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "runtime_automation_pause",
        "Pause or resume automation",
        "Set daemon-owned automation pause state.",
        "runtime.automation_pause",
        _schema({"paused": {"type": "boolean"}}, required=["paused"]),
        _annotations("Pause or resume automation", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "runtime_screen_control_request",
        "Request screen control",
        "Request generic screen-control enablement through daemon-owned state.",
        "runtime.screen_control.request",
        _schema(
            {
                **_optional_string_properties("session_id", "reason"),
            },
        ),
        _annotations("Request screen control", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "source_binding_list",
        "List source bindings",
        "List daemon-owned source/location label bindings.",
        "source_binding.list",
        _EMPTY_INPUT,
        _annotations("List source bindings", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "source_binding_preview",
        "Preview source binding",
        "Preview how a URI resolves through source/location bindings.",
        "source_binding.preview",
        _schema({"uri": {"type": "string"}}, required=["uri"]),
        _annotations("Preview source binding", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "source_binding_upsert",
        "Upsert source binding",
        "Create or update a daemon-owned source/location label binding.",
        "source_binding.upsert",
        _schema(
            {
                "binding": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "name": {"type": "string"},
                        "scope_pattern_canonical": {"type": "string"},
                        "category": {"type": "string"},
                        "default_tier": {"type": "string"},
                        "write_discipline": {"type": "string"},
                    },
                },
            },
            required=["binding"],
        ),
        _annotations("Upsert source binding", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "source_binding_delete",
        "Delete source binding",
        "Delete a daemon-owned source/location label binding.",
        "source_binding.delete",
        _schema({"name": {"type": "string"}}, required=["name"]),
        _annotations("Delete source binding", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "settings_get",
        "Get settings",
        "Return daemon-owned client/operator settings.",
        "settings.get",
        _EMPTY_INPUT,
        _annotations("Get settings", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "settings_update",
        "Update settings",
        "Update daemon-owned client/operator settings.",
        "settings.update",
        _schema(
            {
                "settings": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "default_purpose": {"type": "string"},
                        "launch_at_login": {"type": "boolean"},
                        "notifications_enabled": {"type": "boolean"},
                        "prefer_local_mlx": {"type": "boolean"},
                        "show_thinking_output": {"type": "boolean"},
                        "enable_screen_control": {"type": "boolean"},
                        "require_touch_id_for_high_risk": {"type": "boolean"},
                        "verbose_daemon_logging": {"type": "boolean"},
                    },
                },
            },
            required=["settings"],
        ),
        _annotations("Update settings", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "config_validate",
        "Validate daemon config",
        "Validate daemon config and runtime manifest diagnostics.",
        "config.validate",
        _schema(
            {
                "config_path": {"type": "string"},
            },
        ),
        _annotations("Validate daemon config", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "config_log_locations",
        "Config log locations",
        "Return daemon-owned audit/config log locations.",
        "config.log_locations",
        _EMPTY_INPUT,
        _annotations("Config log locations", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "policy_explain",
        "Explain policy",
        "Explain the active policy context for a session or tool.",
        "policy.explain",
        _schema(
            {
                **_optional_string_properties("session_id", "tool", "capability_kind"),
                "args": {"type": "object", "additionalProperties": True},
            },
        ),
        _annotations("Explain policy", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "provenance_graph",
        "Provenance graph",
        "Return daemon materialized provenance DAG data.",
        "provenance.graph",
        _schema(
            {
                **_optional_string_properties("session_id", "tool", "since"),
                "limit": {"type": "integer", "minimum": 1},
            },
        ),
        _annotations("Provenance graph", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "macos_frontmost_context",
        "macOS frontmost context",
        "Return daemon-owned context for the current frontmost macOS app.",
        "macos.frontmost_context",
        _EMPTY_INPUT,
        _annotations("macOS frontmost context", read_only=True, idempotent=True, open_world=True),
    ),
    ControlToolSpec(
        "audit_list",
        "List audit events",
        "List daemon audit events with optional filters.",
        "audit.list",
        _schema(
            {
                **_optional_string_properties(
                    "event_type",
                    "event_type_contains",
                    "session_id",
                    "since",
                ),
                "limit": {"type": "integer", "minimum": 1},
            },
        ),
        _annotations("List audit events", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "audit_tail",
        "Tail audit events",
        "Return recent daemon audit events.",
        "audit.tail",
        _schema({"limit": {"type": "integer", "minimum": 1}}),
        _annotations("Tail audit events", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "session_list",
        "List sessions",
        "List CapDep sessions.",
        "session.list",
        _schema(
            {
                **_optional_string_properties("state", "owner", "purpose_handle"),
                "include_archived": {"type": "boolean"},
            },
        ),
        _annotations("List sessions", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "session_new",
        "Create session",
        "Create a new CapDep session through the daemon.",
        "session.new",
        _schema(
            {
                **_optional_string_properties("owner", "intent", "purpose_handle"),
                "labels": {"type": "array", "items": {"type": "string"}},
                "first_use_prompts": {"type": "boolean"},
            },
        ),
        _annotations("Create session", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "session_get",
        "Get session",
        "Return details for a CapDep session.",
        "session.get",
        _SESSION_ID_SCHEMA,
        _annotations("Get session", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "session_security_context",
        "Session security context",
        "Return the daemon-owned security context for a CapDep session.",
        "session.security_context",
        _SESSION_ID_SCHEMA,
        _annotations("Session security context", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "session_children",
        "List child sessions",
        "Return delegated child sessions for a CapDep session.",
        "session.children",
        _SESSION_ID_SCHEMA,
        _annotations("List child sessions", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "session_send",
        "Send session message",
        "Send a message through the daemon agent loop for a session.",
        "session.send",
        _SESSION_MESSAGE_SCHEMA,
        _annotations("Send session message", read_only=False, idempotent=False, open_world=True),
    ),
    ControlToolSpec(
        "session_turn_start",
        "Start streamed session turn",
        "Start a daemon-managed session turn with replayable events and heartbeat leases.",
        "session.turn.start",
        _schema(
            {
                **_SESSION_MESSAGE_SCHEMA["properties"],
                "heartbeat_enabled": {"type": "boolean"},
                "heartbeat_interval_seconds": {"type": "number", "minimum": 0},
                "heartbeat_timeout_seconds": {"type": "number", "minimum": 0},
                "admin_override": {"type": "boolean"},
            },
            required=["session_id", "message"],
        ),
        _annotations(
            "Start streamed session turn",
            read_only=False,
            idempotent=False,
            open_world=True,
        ),
    ),
    ControlToolSpec(
        "session_turn_get",
        "Get session turn",
        "Return daemon-owned state for one streamed turn.",
        "session.turn.get",
        _TURN_ID_SCHEMA,
        _annotations("Get session turn", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "session_turn_list",
        "List session turns",
        "List daemon-owned streamed turns, optionally filtered by session or client.",
        "session.turn.list",
        _schema(
            {
                **_optional_string_properties("session_id", "client_id", "status"),
                "active_only": {"type": "boolean"},
            },
        ),
        _annotations("List session turns", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "session_turn_events",
        "List session turn events",
        "Return replayable events for a streamed session turn.",
        "session.turn.events",
        _TURN_EVENTS_SCHEMA,
        _annotations("List session turn events", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "session_turn_ack",
        "Acknowledge session turn heartbeat",
        "Renew the daemon heartbeat lease for a streamed session turn.",
        "session.turn.ack",
        _TURN_ACK_SCHEMA,
        _annotations("Acknowledge session turn heartbeat", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "session_turn_cancel",
        "Cancel streamed session turn",
        "Cancel one daemon-managed streamed session turn.",
        "session.turn.cancel",
        _TURN_CANCEL_SCHEMA,
        _annotations("Cancel streamed session turn", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "workstream_claim",
        "Claim workstream",
        "Claim or renew the daemon-owned primary lease for an interactive workstream.",
        "workstream.claim",
        _WORKSTREAM_SESSION_SCHEMA,
        _annotations("Claim workstream", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "workstream_ensure",
        "Ensure workstream",
        "Ensure a client has a live interactive workstream lease, auto-claiming if needed.",
        "workstream.ensure",
        _WORKSTREAM_SESSION_SCHEMA,
        _annotations("Ensure workstream", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "workstream_renew",
        "Renew workstream",
        "Renew an existing workstream lease.",
        "workstream.renew",
        _schema(
            {
                **_WORKSTREAM_ID_SCHEMA["properties"],
                **_optional_string_properties("client_id", "lease_token"),
                "lease_seconds": {"type": "integer", "minimum": 1},
                "admin_override": {"type": "boolean"},
            },
            required=["workstream_id"],
        ),
        _annotations("Renew workstream", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "workstream_release",
        "Release workstream",
        "Release a workstream lease back to the daemon.",
        "workstream.release",
        _schema(
            {
                **_WORKSTREAM_ID_SCHEMA["properties"],
                **_optional_string_properties("client_id", "lease_token", "reason"),
                "admin_override": {"type": "boolean"},
            },
            required=["workstream_id"],
        ),
        _annotations("Release workstream", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "workstream_get",
        "Get workstream",
        "Return details for an interactive workstream.",
        "workstream.get",
        _WORKSTREAM_ID_SCHEMA,
        _annotations("Get workstream", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "workstream_list",
        "List workstreams",
        "List daemon-owned workstreams by session or client.",
        "workstream.list",
        _schema(
            {
                **_optional_string_properties("session_id", "client_id"),
                "active_only": {"type": "boolean"},
            },
        ),
        _annotations("List workstreams", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "workstream_release_client",
        "Release client workstreams",
        "Release all active daemon-owned workstreams held by a client.",
        "workstream.release_client",
        _schema(
            {
                "client_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            required=["client_id"],
        ),
        _annotations(
            "Release client workstreams",
            read_only=False,
            idempotent=True,
            destructive=True,
        ),
    ),
    ControlToolSpec(
        "workstream_sweep_expired",
        "Sweep expired workstreams",
        "Retire expired daemon-owned workstream leases.",
        "workstream.sweep_expired",
        _EMPTY_INPUT,
        _annotations("Sweep expired workstreams", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "session_cancel",
        "Cancel session turn",
        "Cancel an active daemon agent turn for a session.",
        "session.cancel",
        _SESSION_ID_SCHEMA,
        _annotations("Cancel session turn", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "session_fork",
        "Fork session",
        "Fork a child session through the daemon.",
        "session.fork",
        _schema(
            {
                "parent_id": {"type": "string"},
                "intent": {"type": "string"},
            },
            required=["parent_id"],
        ),
        _annotations("Fork session", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "session_pause",
        "Pause session",
        "Pause a CapDep session.",
        "session.pause",
        _SESSION_ID_SCHEMA,
        _annotations("Pause session", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "session_resume",
        "Resume session",
        "Resume a paused CapDep session.",
        "session.resume",
        _SESSION_ID_SCHEMA,
        _annotations("Resume session", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "session_abort",
        "Abort session",
        "Abort a CapDep session.",
        "session.abort",
        _SESSION_ID_SCHEMA,
        _annotations("Abort session", read_only=False, idempotent=True, destructive=True),
    ),
    ControlToolSpec(
        "session_add_labels",
        "Add session labels",
        "Add labels to a CapDep session.",
        "session.add_labels",
        _schema(
            {
                "session_id": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            required=["session_id", "labels"],
        ),
        _annotations("Add session labels", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "session_set_enforcement",
        "Set session enforcement",
        "Set daemon policy enforcement mode for a session.",
        "session.set_enforcement",
        _schema(
            {
                "session_id": {"type": "string"},
                "mode": {
                    "type": "string",
                    "description": "Daemon-supported enforcement mode.",
                },
            },
            required=["session_id", "mode"],
        ),
        _annotations("Set session enforcement", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "session_set_first_use_prompts",
        "Set first-use prompts",
        "Enable or disable first-use prompts for a session.",
        "session.set_first_use_prompts",
        _schema(
            {
                "session_id": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
            required=["session_id", "enabled"],
        ),
        _annotations("Set first-use prompts", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "session_delegate",
        "Delegate capability",
        "Delegate an attenuated capability from parent session to child session.",
        "session.delegate",
        _schema(
            {
                "parent_session_id": {"type": "string"},
                "child_session_id": {"type": "string"},
                "kind": {"type": "string"},
                "pattern": {"type": "string"},
                "max_amount": {"type": "integer"},
                "expires_at": {"type": "string"},
                "expiry": {"type": "string"},
                "rate_limit": {"type": "object", "additionalProperties": True},
                "add_revoked_by": {"type": "array", "items": {"type": "string"}},
            },
            required=["parent_session_id", "child_session_id", "kind"],
        ),
        _annotations("Delegate capability", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "session_grant_capability",
        "Grant session capability",
        "Grant a fully specified capability through daemon operator authority.",
        "session.grant_capability",
        _schema(
            {
                "session_id": {"type": "string"},
                "capability": {"type": "object", "additionalProperties": True},
            },
            required=["session_id", "capability"],
        ),
        _annotations("Grant session capability", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "capability_revoke",
        "Revoke capability",
        "Revoke a session capability by audit id.",
        "capability.revoke",
        _schema(
            {
                "session_id": {"type": "string"},
                "audit_id": {"type": "string"},
                "trigger": {"type": "string"},
            },
            required=["session_id", "audit_id"],
        ),
        _annotations("Revoke capability", read_only=False, idempotent=True, destructive=True),
    ),
    ControlToolSpec(
        "memory_entries",
        "List daemon memory",
        "List daemon memory keys and labels.",
        "memory.entries",
        _EMPTY_INPUT,
        _annotations("List daemon memory", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "policy_show",
        "Show policy",
        "Show daemon policy metadata.",
        "policy.show",
        _EMPTY_INPUT,
        _annotations("Show policy", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "policy_test",
        "Test policy",
        "Run a daemon policy simulation.",
        "policy.test",
        _schema(
            {
                "action_kind": {"type": "string"},
                "target": {"type": "string"},
                "amount": {"type": "integer"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "capabilities": {"type": "array", "items": {"type": "object"}},
            },
            required=["action_kind", "target"],
        ),
        _annotations("Test policy", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "policy_validate",
        "Validate policy",
        "Validate daemon policy invariants.",
        "policy.validate",
        _EMPTY_INPUT,
        _annotations("Validate policy", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "tool_list",
        "List daemon tools",
        "List tools available through the CapDep daemon.",
        "tool.list",
        _schema({"session_id": {"type": "string"}}),
        _annotations("List daemon tools", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "tool_show",
        "Show daemon tool",
        "Show metadata for a daemon tool in a session.",
        "tool.show",
        _schema(
            {
                "session_id": {"type": "string"},
                "tool": {"type": "string"},
            },
            required=["session_id", "tool"],
        ),
        _annotations("Show daemon tool", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "tool_test",
        "Test daemon tool",
        "Run a daemon-owned tool smoke test through policy-aware daemon logic.",
        "tool.test",
        _schema(
            {
                "session_id": {"type": "string"},
                "tool": {"type": "string"},
                "args": {"type": "object", "additionalProperties": True},
            },
            required=["session_id", "tool"],
        ),
        _annotations("Test daemon tool", read_only=False, idempotent=False, open_world=True),
    ),
    ControlToolSpec(
        "tool_call",
        "Call daemon tool",
        "Call a CapDep daemon tool inside a session. The daemon enforces policy.",
        "tool.call",
        _schema(
            {
                "session_id": {"type": "string"},
                "tool": {"type": "string"},
                "args": {"type": "object", "additionalProperties": True},
            },
            required=["session_id", "tool"],
        ),
        _annotations(
            "Call daemon tool",
            read_only=False,
            idempotent=False,
            destructive=True,
            open_world=True,
        ),
    ),
    ControlToolSpec(
        "approval_list",
        "List approvals",
        "List pending and recent daemon approval requests.",
        "approval.list",
        _schema(
            {
                **_optional_string_properties("session_id", "status"),
                "limit": {"type": "integer", "minimum": 1},
            },
        ),
        _annotations("List approvals", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "approval_show",
        "Show approval",
        "Show a daemon approval request.",
        "approval.show",
        _schema({"id": {"type": "integer"}}, required=["id"]),
        _annotations("Show approval", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "approval_detail",
        "Approval detail",
        "Return GUI-grade approval details from the daemon.",
        "approval.detail",
        _schema({"id": {"type": "integer"}}, required=["id"]),
        _annotations("Approval detail", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "approval_approve",
        "Approve request",
        "Approve a daemon approval request.",
        "approval.approve",
        _APPROVAL_ID_SCHEMA,
        _annotations("Approve request", read_only=False, idempotent=False, destructive=True),
    ),
    ControlToolSpec(
        "approval_deny",
        "Deny request",
        "Deny a daemon approval request.",
        "approval.deny",
        _schema(
            {
                "id": {"type": "integer"},
                "decided_by": {"type": "string", "default": "mcp-control"},
                "reason": {"type": "string"},
            },
            required=["id"],
        ),
        _annotations("Deny request", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "approval_defer",
        "Defer request",
        "Defer a daemon approval request.",
        "approval.defer",
        _schema(
            {
                "id": {"type": "integer"},
                "decided_by": {"type": "string", "default": "mcp-control"},
                "reason": {"type": "string"},
            },
            required=["id"],
        ),
        _annotations("Defer request", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "approval_approve_group",
        "Approve approval group",
        "Approve a daemon approval group.",
        "approval.approve_group",
        _schema(
            {
                "group_id": {"type": "string"},
                "decided_by": {"type": "string", "default": "mcp-control"},
                "strong_auth": {"type": "string"},
            },
            required=["group_id"],
        ),
        _annotations(
            "Approve approval group",
            read_only=False,
            idempotent=False,
            destructive=True,
        ),
    ),
    ControlToolSpec(
        "approval_pattern_list",
        "List approval patterns",
        "List daemon approval pattern rules.",
        "approval_pattern.list",
        _EMPTY_INPUT,
        _annotations("List approval patterns", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "approval_pattern_create",
        "Create approval pattern",
        "Create a daemon approval pattern rule.",
        "approval_pattern.create",
        _schema(
            {
                "action": {"type": "string"},
                "target_pattern": {"type": "string"},
                "ttl_hours": {"type": "number"},
                "created_by": {"type": "string"},
                "payload_pattern": {"type": "string"},
                "labels_required": {"type": "array", "items": {"type": "string"}},
                "audit_tag": {"type": "string"},
            },
            required=["action", "target_pattern"],
        ),
        _annotations("Create approval pattern", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "approval_pattern_revoke",
        "Revoke approval pattern",
        "Revoke a daemon approval pattern rule.",
        "approval_pattern.revoke",
        _schema({"id": {"type": "string"}}, required=["id"]),
        _annotations("Revoke approval pattern", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "approval_pattern_import",
        "Import approval patterns",
        "Import daemon approval pattern rules from a library file.",
        "approval_pattern.import",
        _schema({"path": {"type": "string"}}, required=["path"]),
        _annotations("Import approval patterns", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "override_list",
        "List overrides",
        "List daemon override grants.",
        "override.list",
        _EMPTY_INPUT,
        _annotations("List overrides", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "override_show",
        "Show override",
        "Show one daemon override grant.",
        "override.show",
        _GRANT_ID_SCHEMA,
        _annotations("Show override", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "override_request",
        "Request override",
        "Request a daemon hard-floor override.",
        "override.request",
        _schema(
            {
                "session_id": {"type": "string"},
                "action_kind": {"type": "string"},
                "target": {"type": "string"},
                "floor": {"type": "string"},
                "invoker": {"type": "string"},
                "category": {"type": "string"},
                "tier": {"type": "string"},
                "friction_confirmed": {"type": "boolean"},
            },
            required=["session_id", "action_kind", "target", "floor", "invoker"],
        ),
        _annotations("Request override", read_only=False, idempotent=False, destructive=True),
    ),
    ControlToolSpec(
        "override_attest",
        "Attest override",
        "Attest or refuse a pending override grant.",
        "override.attest",
        _schema(
            {
                "grant_id": {"type": "string"},
                "attester": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
            required=["grant_id", "attester"],
        ),
        _annotations("Attest override", read_only=False, idempotent=False, destructive=True),
    ),
    ControlToolSpec(
        "override_refuse",
        "Refuse override",
        "Refuse a daemon override grant.",
        "override.refuse",
        _GRANT_ID_SCHEMA,
        _annotations("Refuse override", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "override_sweep",
        "Sweep overrides",
        "Expire stale daemon override grants.",
        "override.sweep",
        _EMPTY_INPUT,
        _annotations("Sweep overrides", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "relationship_group_list",
        "List relationship groups",
        "List daemon relationship groups.",
        "relationship_group.list",
        _EMPTY_INPUT,
        _annotations("List relationship groups", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "relationship_group_add_member",
        "Add relationship member",
        "Add a principal to a daemon relationship group.",
        "relationship_group.add_member",
        _RELATIONSHIP_MEMBER_SCHEMA,
        _annotations("Add relationship member", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "relationship_group_remove_member",
        "Remove relationship member",
        "Remove a principal from a daemon relationship group.",
        "relationship_group.remove_member",
        _RELATIONSHIP_MEMBER_SCHEMA,
        _annotations("Remove relationship member", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "relationship_group_tier",
        "Relationship member tier",
        "Show a principal tier within a relationship group.",
        "relationship_group.tier",
        _RELATIONSHIP_MEMBER_SCHEMA,
        _annotations("Relationship member tier", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "relationship_group_effective_tier",
        "Relationship effective tier",
        "Show a principal's effective relationship tier.",
        "relationship_group.effective_tier",
        _schema({"principal_id": {"type": "string"}}, required=["principal_id"]),
        _annotations("Relationship effective tier", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "relationship_group_promote",
        "Promote relationship member",
        "Set a principal tier within a relationship group.",
        "relationship_group.promote",
        _schema(
            {
                "group_id": {"type": "string"},
                "principal_id": {"type": "string"},
                "tier": {"type": "string"},
            },
            required=["group_id", "principal_id", "tier"],
        ),
        _annotations("Promote relationship member", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "relationship_group_aggregate_audit",
        "Relationship audit summary",
        "Aggregate audit counts for a relationship principal.",
        "relationship_group.aggregate_audit",
        _schema({"principal_id": {"type": "string"}}, required=["principal_id"]),
        _annotations("Relationship audit summary", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "demo_list_scenarios",
        "List demos",
        "List daemon demo scenarios.",
        "demo.list_scenarios",
        _EMPTY_INPUT,
        _annotations("List demos", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "demo_start",
        "Start demo",
        "Start a daemon demo scenario.",
        "demo.start",
        _schema({"name": {"type": "string"}}, required=["name"]),
        _annotations("Start demo", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "extract_schemas",
        "List extract schemas",
        "List quarantined extraction schemas.",
        "extract.schemas",
        _EMPTY_INPUT,
        _annotations("List extract schemas", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "extract_inbox_ids",
        "List extract inbox messages",
        "List inbox messages available to quarantined extraction.",
        "extract.inbox_ids",
        _EMPTY_INPUT,
        _annotations("List extract inbox messages", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "extract_inbox_message",
        "Extract inbox message",
        "Run quarantined extraction for an inbox message.",
        "extract.inbox_message",
        _schema(
            {
                "message_id": {"type": "string"},
                "schema": {"type": "string"},
            },
            required=["message_id", "schema"],
        ),
        _annotations("Extract inbox message", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "devbox_summary_for_all",
        "Devbox summary",
        "Return daemon devbox workspace/container summary.",
        "devbox.summary_for_all",
        _EMPTY_INPUT,
        _annotations("Devbox summary", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_registry_list",
        "List onguard clients",
        "List daemon-admitted clients, optionally filtered to onguard clients.",
        "client.registry.list",
        _schema({"kind": {"type": "string", "default": "onguard"}}),
        _annotations("List onguard clients", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_registry_register",
        "Register onguard client",
        "Register or update a daemon-admitted onguard client identity.",
        "client.registry.register",
        _schema(
            {
                "client_id": _ONGUARD_CLIENT_ID,
                "kind": {"type": "string", "default": "onguard"},
                "display_name": {"type": "string"},
                "metadata": _JSON_OBJECT_SCHEMA,
                "status": {"type": "string"},
            },
            required=["client_id"],
        ),
        _annotations("Register onguard client", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_config_list",
        "List onguard config",
        "List daemon-owned onguard client configuration entries.",
        "client.config.list",
        _schema({"client_id": _ONGUARD_CLIENT_ID}),
        _annotations("List onguard config", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_config_propose",
        "Propose onguard config",
        "Propose daemon-owned onguard client configuration for approval.",
        "client.config.propose",
        _schema(
            {
                "client_id": _ONGUARD_CLIENT_ID,
                "key": {"type": "string"},
                "value": _JSON_OBJECT_SCHEMA,
                "proposed_by": {"type": "string", "default": "mcp-control"},
            },
            required=["client_id", "key", "value"],
        ),
        _annotations("Propose onguard config", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_config_approve",
        "Approve onguard config",
        "Approve a daemon-owned onguard client configuration proposal.",
        "client.config.approve",
        _schema(
            {
                "config_id": {"type": "string"},
                "approved_by": {"type": "string", "default": "mcp-control"},
            },
            required=["config_id"],
        ),
        _annotations("Approve onguard config", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_queue_list",
        "List onguard queue",
        "List daemon-owned onguard queued commands.",
        "client.queue.list",
        _schema({"client_id": _ONGUARD_CLIENT_ID, "status": {"type": "string"}}),
        _annotations("List onguard queue", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_queue_enqueue",
        "Enqueue onguard command",
        "Enqueue a daemon-owned onguard client command.",
        "client.queue.enqueue",
        _schema(
            {
                "client_id": _ONGUARD_CLIENT_ID,
                "command": {"type": "string"},
                "payload": _JSON_OBJECT_SCHEMA,
                "labels": _LABELS_SCHEMA,
                "provenance": _JSON_OBJECT_SCHEMA,
            },
            required=["client_id", "command"],
        ),
        _annotations("Enqueue onguard command", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_queue_claim",
        "Claim onguard command",
        "Claim one daemon-owned onguard queued command for a worker.",
        "client.queue.claim",
        _schema(
            {
                "client_id": _ONGUARD_CLIENT_ID,
                "claimed_by": {"type": "string"},
                "lease_seconds": {"type": "integer", "minimum": 1},
            },
            required=["client_id", "claimed_by"],
        ),
        _annotations("Claim onguard command", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_queue_complete",
        "Complete onguard command",
        "Mark a daemon-owned onguard queued command complete.",
        "client.queue.complete",
        _schema(
            {
                "command_id": _ONGUARD_COMMAND_ID,
                "result": _JSON_OBJECT_SCHEMA,
                "artifact_ref": {"type": "string"},
            },
            required=["command_id"],
        ),
        _annotations("Complete onguard command", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_queue_fail",
        "Fail onguard command",
        "Mark a daemon-owned onguard queued command failed.",
        "client.queue.fail",
        _schema(
            {
                "command_id": _ONGUARD_COMMAND_ID,
                "result": _JSON_OBJECT_SCHEMA,
                "artifact_ref": {"type": "string"},
            },
            required=["command_id"],
        ),
        _annotations("Fail onguard command", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_events_list",
        "List onguard events",
        "List daemon-owned onguard events and results.",
        "client.events.list",
        _schema(
            {
                "client_id": _ONGUARD_CLIENT_ID,
                "event_type": {"type": "string"},
                "include_acked": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1},
            },
        ),
        _annotations("List onguard events", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_events_ack",
        "Acknowledge onguard event",
        "Acknowledge a daemon-owned onguard event.",
        "client.events.ack",
        _schema(
            {
                "event_id": {"type": "string"},
                "acked_by": {"type": "string", "default": "mcp-control"},
            },
            required=["event_id"],
        ),
        _annotations("Acknowledge onguard event", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_events_publish",
        "Publish onguard event",
        "Publish a daemon-owned onguard client event or result notification.",
        "client.events.publish",
        _schema(
            {
                "client_id": _ONGUARD_CLIENT_ID,
                "command_id": _ONGUARD_COMMAND_ID,
                "schedule_id": _ONGUARD_SCHEDULE_ID,
                "event_type": {"type": "string"},
                "payload": _JSON_OBJECT_SCHEMA,
                "labels": _LABELS_SCHEMA,
            },
            required=["client_id", "event_type"],
        ),
        _annotations("Publish onguard event", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_artifact_list",
        "List onguard artifacts",
        "List daemon-owned onguard artifacts.",
        "artifact.list",
        _schema(
            {
                "client_id": _ONGUARD_CLIENT_ID,
                "artifact_type": {"type": "string"},
                "status": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
        ),
        _annotations("List onguard artifacts", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_artifact_read",
        "Read onguard artifact",
        "Read one daemon-owned onguard artifact.",
        "artifact.read",
        _schema({"artifact_id": _ONGUARD_ARTIFACT_ID}, required=["artifact_id"]),
        _annotations("Read onguard artifact", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_artifact_create",
        "Create onguard artifact",
        "Create a daemon-owned onguard artifact.",
        "artifact.create",
        _schema(
            {
                "client_id": _ONGUARD_CLIENT_ID,
                "command_id": _ONGUARD_COMMAND_ID,
                "schedule_id": _ONGUARD_SCHEDULE_ID,
                "artifact_type": {"type": "string"},
                "content": _JSON_OBJECT_SCHEMA,
                "labels": _LABELS_SCHEMA,
                "provenance": _JSON_OBJECT_SCHEMA,
            },
            required=["client_id", "artifact_type", "content"],
        ),
        _annotations("Create onguard artifact", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_artifact_promote",
        "Promote onguard artifact",
        "Promote a daemon-owned onguard artifact after approval/review.",
        "artifact.promote",
        _schema(
            {
                "artifact_id": _ONGUARD_ARTIFACT_ID,
                "promoted_by": {"type": "string", "default": "mcp-control"},
            },
            required=["artifact_id"],
        ),
        _annotations("Promote onguard artifact", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_artifact_delete",
        "Delete onguard artifact",
        "Delete a daemon-owned onguard artifact record.",
        "artifact.delete",
        _schema({"artifact_id": _ONGUARD_ARTIFACT_ID}, required=["artifact_id"]),
        _annotations("Delete onguard artifact", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_schedule_list",
        "List onguard schedules",
        "List daemon-owned onguard schedules.",
        "schedule.list",
        _schema({"client_id": _ONGUARD_CLIENT_ID, "active": {"type": "boolean"}}),
        _annotations("List onguard schedules", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_schedule_create",
        "Create onguard schedule",
        "Create a daemon-owned onguard schedule.",
        "schedule.create",
        _schema(
            {
                "schedule_id": _ONGUARD_SCHEDULE_ID,
                "client_id": _ONGUARD_CLIENT_ID,
                "command": {"type": "string"},
                "recurrence": _JSON_OBJECT_SCHEMA,
                "payload": _JSON_OBJECT_SCHEMA,
                "labels": _LABELS_SCHEMA,
                "approved_by": {"type": "string"},
                "status": {"type": "string"},
                "next_run_at": {"type": "string"},
                "created_by": {"type": "string", "default": "mcp-control"},
            },
            required=["schedule_id", "client_id", "command", "recurrence"],
        ),
        _annotations("Create onguard schedule", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_schedule_update",
        "Update onguard schedule",
        "Update a daemon-owned onguard schedule.",
        "schedule.update",
        _schema(
            {
                "schedule_id": _ONGUARD_SCHEDULE_ID,
                "name": {"type": "string"},
                "recurrence": _JSON_OBJECT_SCHEMA,
                "payload": _JSON_OBJECT_SCHEMA,
                "labels": _LABELS_SCHEMA,
                "provenance": _JSON_OBJECT_SCHEMA,
                "active": {"type": "boolean"},
                "next_run_at": {"type": "string"},
            },
            required=["schedule_id"],
        ),
        _annotations("Update onguard schedule", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_schedule_disable",
        "Disable onguard schedule",
        "Disable a daemon-owned onguard schedule.",
        "schedule.disable",
        _schema({"schedule_id": _ONGUARD_SCHEDULE_ID}, required=["schedule_id"]),
        _annotations("Disable onguard schedule", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_schedule_run_now",
        "Run onguard schedule now",
        "Queue one immediate run of a daemon-owned onguard schedule.",
        "schedule.run_now",
        _schema(
            {
                "schedule_id": _ONGUARD_SCHEDULE_ID,
                "requested_by": {"type": "string", "default": "mcp-control"},
            },
            required=["schedule_id"],
        ),
        _annotations("Run onguard schedule now", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_schedule_claim_due",
        "Claim due onguard schedule",
        "Claim one due daemon-owned onguard schedule for a worker.",
        "schedule.claim_due",
        _schema(
            {
                "client_id": _ONGUARD_CLIENT_ID,
                "claimed_by": {"type": "string"},
                "lease_seconds": {"type": "integer", "minimum": 1},
            },
            required=["client_id", "claimed_by"],
        ),
        _annotations("Claim due onguard schedule", read_only=False, idempotent=False),
    ),
    ControlToolSpec(
        "onguard_schedule_complete_run",
        "Complete onguard schedule run",
        "Mark a daemon-owned onguard schedule run complete.",
        "schedule.complete_run",
        _schema(
            {
                "run_id": {"type": "string"},
                "result": _JSON_OBJECT_SCHEMA,
                "artifact_ref": {"type": "string"},
            },
            required=["run_id"],
        ),
        _annotations("Complete onguard schedule run", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_schedule_fail_run",
        "Fail onguard schedule run",
        "Mark a daemon-owned onguard schedule run failed.",
        "schedule.fail_run",
        _schema(
            {
                "run_id": {"type": "string"},
                "result": _JSON_OBJECT_SCHEMA,
                "error": {"type": "string"},
                "artifact_ref": {"type": "string"},
            },
            required=["run_id"],
        ),
        _annotations("Fail onguard schedule run", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "onguard_schedule_history",
        "Onguard schedule history",
        "List daemon-owned onguard schedule run history.",
        "schedule.history",
        _schema(
            {
                "schedule_id": _ONGUARD_SCHEDULE_ID,
                "client_id": _ONGUARD_CLIENT_ID,
                "limit": {"type": "integer", "minimum": 1},
            },
        ),
        _annotations("Onguard schedule history", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "programmatic_dry_run",
        "Programmatic dry run",
        "Dry-run a programmatic-mode source file through the daemon.",
        "programmatic.dry_run",
        _GENERIC_ARGS_SCHEMA,
        _annotations("Programmatic dry run", read_only=True, idempotent=False),
    ),
    ControlToolSpec(
        "programmatic_run",
        "Programmatic run",
        "Run a programmatic-mode source file through the daemon.",
        "programmatic.run",
        _GENERIC_ARGS_SCHEMA,
        _annotations("Programmatic run", read_only=False, idempotent=False, destructive=True),
    ),
    ControlToolSpec(
        "programmatic_bundle_dry_run",
        "Bundle dry run",
        "Dry-run a programmatic approval bundle through the daemon.",
        "programmatic.bundle_dry_run",
        _GENERIC_ARGS_SCHEMA,
        _annotations("Bundle dry run", read_only=True, idempotent=False),
    ),
    ControlToolSpec(
        "programmatic_bundle_execute",
        "Bundle execute",
        "Execute an approved programmatic bundle through the daemon.",
        "programmatic.bundle_execute",
        _GENERIC_ARGS_SCHEMA,
        _annotations("Bundle execute", read_only=False, idempotent=False, destructive=True),
    ),
    ControlToolSpec(
        "programmatic_bundle_run",
        "Bundle run",
        "Dry-run or execute a programmatic bundle through the daemon.",
        "programmatic.bundle_run",
        _GENERIC_ARGS_SCHEMA,
        _annotations("Bundle run", read_only=False, idempotent=False, destructive=True),
    ),
    ControlToolSpec(
        "google_oauth_status",
        "Google OAuth status",
        "Return daemon-owned Google Workspace MCP OAuth status for all services or one service.",
        "setup.google.oauth_status",
        _schema({"service_id": _GOOGLE_SERVICE_ID}),
        _annotations("Google OAuth status", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "google_configure_oauth_client",
        "Configure Google OAuth client",
        "Store Google OAuth client values for a managed Workspace MCP server.",
        "setup.google.configure_oauth",
        _schema(
            {
                "service_id": _GOOGLE_SERVICE_ID,
                "client_id": {"type": "string"},
                "client_secret": {"type": "string"},
            },
            required=["service_id", "client_id", "client_secret"],
        ),
        _annotations("Configure Google OAuth client", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "google_oauth_login",
        "Authorize Google OAuth",
        "Launch the daemon-owned browser OAuth flow for a managed Workspace MCP server.",
        "setup.google.oauth_login",
        _schema(
            {
                "service_id": _GOOGLE_SERVICE_ID,
                "open_browser": {"type": "boolean", "default": True},
                "timeout_seconds": {"type": "integer", "minimum": 1, "default": 180},
            },
            required=["service_id"],
        ),
        _annotations(
            "Authorize Google OAuth",
            read_only=False,
            idempotent=False,
            open_world=True,
        ),
    ),
    ControlToolSpec(
        "google_oauth_revoke",
        "Revoke Google OAuth token",
        "Remove the local OAuth token cache for a managed Workspace MCP server.",
        "setup.google.oauth_revoke",
        _schema({"service_id": _GOOGLE_SERVICE_ID}, required=["service_id"]),
        _annotations("Revoke Google OAuth token", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "gmail_oauth_status",
        "Gmail OAuth status",
        "Return daemon-owned Google Gmail MCP OAuth configuration status.",
        "setup.google_gmail.oauth_status",
        _EMPTY_INPUT,
        _annotations("Gmail OAuth status", read_only=True, idempotent=True),
    ),
    ControlToolSpec(
        "gmail_configure_oauth_client",
        "Configure Gmail OAuth client",
        "Store Google OAuth client values through the daemon.",
        "setup.google_gmail.configure_oauth",
        _schema(
            {
                "client_id": {"type": "string"},
                "client_secret": {"type": "string"},
            },
            required=["client_id", "client_secret"],
        ),
        _annotations("Configure Gmail OAuth client", read_only=False, idempotent=True),
    ),
    ControlToolSpec(
        "gmail_oauth_login",
        "Authorize Gmail OAuth",
        "Launch the daemon-owned browser OAuth flow for Gmail MCP.",
        "setup.google_gmail.oauth_login",
        _schema(
            {
                "open_browser": {"type": "boolean", "default": True},
                "timeout_seconds": {"type": "integer", "minimum": 1, "default": 180},
            },
        ),
        _annotations(
            "Authorize Gmail OAuth",
            read_only=False,
            idempotent=False,
            open_world=True,
        ),
    ),
)

_CONTROL_TOOLS: tuple[mcp_types.Tool, ...] = tuple(_tool(spec) for spec in _CONTROL_TOOL_SPECS)
_SPECS_BY_NAME: dict[str, ControlToolSpec] = {spec.name: spec for spec in _CONTROL_TOOL_SPECS}


def discover_control_tools() -> list[mcp_types.Tool]:
    return list(_CONTROL_TOOLS)


async def dispatch_control_tool(
    client: DaemonClient,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> mcp_types.CallToolResult:
    spec = _SPECS_BY_NAME.get(name)
    if spec is None:
        return _error_result(f"unknown control tool: {name}")

    try:
        params = _params_for(name, arguments or {})
        result = await client.call(spec.rpc, params)
    except Exception as e:
        return _error_result(str(e))

    return _ok_result(result)


def _params_for(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    if name in {
        "capdep_ping",
        "capdep_version",
        "daemon_info",
        "daemon_state",
        "app_status",
        "setup_status",
        "setup_plan",
        "setup_check",
        "workflow_templates",
        "connector_status",
        "runtime_status",
        "source_binding_list",
        "memory_entries",
        "policy_show",
        "policy_validate",
        "approval_pattern_list",
        "override_list",
        "override_sweep",
        "relationship_group_list",
        "demo_list_scenarios",
        "extract_schemas",
        "extract_inbox_ids",
        "devbox_summary_for_all",
    }:
        return None
    if name in {"gmail_oauth_status", "macos_frontmost_context"}:
        return None
    if name == "google_oauth_status":
        return _copy(args, "service_id") if args.get("service_id") else None
    if name == "google_configure_oauth_client":
        return _copy(args, "service_id", "client_id", "client_secret")
    if name == "google_oauth_login":
        return {
            "service_id": str(args.get("service_id") or ""),
            "open_browser": bool(args.get("open_browser", True)),
            "timeout_seconds": int(args.get("timeout_seconds") or 180),
        }
    if name == "google_oauth_revoke":
        return {"service_id": str(args.get("service_id") or "")}
    if name == "setup_run_action":
        return {"action_id": str(args.get("action_id") or "")}
    if name == "runtime_automation_pause":
        return {"paused": bool(args.get("paused"))}
    if name == "runtime_screen_control_request":
        return _copy(args, "session_id", "reason")
    if name == "source_binding_preview":
        return {"uri": str(args.get("uri") or "")}
    if name == "source_binding_upsert":
        return {"binding": dict(args.get("binding") or {})}
    if name == "source_binding_delete":
        return {"name": str(args.get("name") or "")}
    if name in {
        "session_get",
        "session_children",
        "session_pause",
        "session_resume",
        "session_abort",
    }:
        return {"session_id": str(args.get("session_id") or "")}
    if name == "session_new":
        params = _copy(args, "owner", "intent", "purpose_handle")
        if "labels" in args:
            params["labels"] = [str(label) for label in args.get("labels") or []]
        if "first_use_prompts" in args:
            params["first_use_prompts"] = bool(args["first_use_prompts"])
        return params
    if name == "session_send":
        return _session_message_params(args)
    if name == "session_turn_start":
        params = _session_message_params(args)
        for key in (
            "heartbeat_enabled",
            "heartbeat_interval_seconds",
            "heartbeat_timeout_seconds",
            "admin_override",
        ):
            if args.get(key) is not None:
                params[key] = args[key]
        return params
    if name == "session_turn_get":
        return {"turn_id": str(args.get("turn_id") or "")}
    if name == "session_turn_list":
        params = _copy(args, "session_id", "client_id", "status")
        if args.get("active_only") is not None:
            params["active_only"] = bool(args["active_only"])
        return params
    if name == "session_turn_events":
        params = {"turn_id": str(args.get("turn_id") or "")}
        if args.get("after") is not None:
            params["after"] = int(args["after"])
        return params
    if name == "session_turn_ack":
        return {
            "turn_id": str(args.get("turn_id") or ""),
            "client_id": str(args.get("client_id") or ""),
        }
    if name == "session_turn_cancel":
        params = {"turn_id": str(args.get("turn_id") or "")}
        if args.get("reason"):
            params["reason"] = str(args["reason"])
        if args.get("client_id"):
            params["client_id"] = str(args["client_id"])
        if args.get("admin_override") is not None:
            params["admin_override"] = bool(args["admin_override"])
        return params
    if name == "session_cancel":
        return {"session_id": str(args.get("session_id") or "")}
    return _params_for_continued(name, args)


def _session_message_params(args: dict[str, Any]) -> dict[str, Any]:
    params = {
        "session_id": str(args.get("session_id") or ""),
        "message": str(args.get("message") or ""),
    }
    if args.get("mode"):
        params["mode"] = str(args["mode"])
    if args.get("max_iterations") is not None:
        params["max_iterations"] = int(args["max_iterations"])
    if args.get("client_id"):
        params["client_id"] = str(args["client_id"])
    if args.get("workstream_id"):
        params["workstream_id"] = str(args["workstream_id"])
    if args.get("lease_token"):
        params["lease_token"] = str(args["lease_token"])
    if args.get("lease_seconds") is not None:
        params["lease_seconds"] = int(args["lease_seconds"])
    if args.get("claim_if_missing") is not None:
        params["claim_if_missing"] = bool(args["claim_if_missing"])
    if args.get("admin_override") is not None:
        params["admin_override"] = bool(args["admin_override"])
    return params


def _params_for_continued(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    if name == "session_fork":
        params = {"parent_id": str(args.get("parent_id") or "")}
        if args.get("intent"):
            params["intent"] = str(args["intent"])
        return params
    if name == "session_add_labels":
        return {
            "session_id": str(args.get("session_id") or ""),
            "labels": [str(label) for label in args.get("labels") or []],
        }
    if name == "session_set_enforcement":
        return {
            "session_id": str(args.get("session_id") or ""),
            "mode": str(args.get("mode") or ""),
        }
    if name == "session_set_first_use_prompts":
        return {
            "session_id": str(args.get("session_id") or ""),
            "enabled": bool(args.get("enabled")),
        }
    if name == "workstream_claim":
        params = {
            "session_id": str(args.get("session_id") or ""),
            "client_id": str(args.get("client_id") or ""),
            "lease_seconds": (
                int(args["lease_seconds"]) if args.get("lease_seconds") is not None else None
            ),
            "lease_token": str(args.get("lease_token")) if args.get("lease_token") else None,
            "reason": str(args.get("reason")) if args.get("reason") else None,
            "workstream_id": str(args.get("workstream_id")) if args.get("workstream_id") else None,
        }
        if args.get("admin_override") is not None:
            params["admin_override"] = bool(args["admin_override"])
        return params
    if name == "workstream_ensure":
        params = {
            "session_id": str(args.get("session_id") or ""),
        }
        if args.get("client_id"):
            params["client_id"] = str(args["client_id"])
        if args.get("lease_seconds") is not None:
            params["lease_seconds"] = int(args["lease_seconds"])
        if args.get("lease_token"):
            params["lease_token"] = str(args["lease_token"])
        if args.get("reason"):
            params["reason"] = str(args["reason"])
        if args.get("workstream_id"):
            params["workstream_id"] = str(args["workstream_id"])
        if args.get("auto_claim") is not None:
            params["auto_claim"] = bool(args["auto_claim"])
        if args.get("admin_override") is not None:
            params["admin_override"] = bool(args["admin_override"])
        return params
    if name == "workstream_renew":
        params = {"workstream_id": str(args.get("workstream_id") or "")}
        if args.get("client_id"):
            params["client_id"] = str(args["client_id"])
        if args.get("lease_token"):
            params["lease_token"] = str(args["lease_token"])
        if args.get("lease_seconds") is not None:
            params["lease_seconds"] = int(args["lease_seconds"])
        if args.get("admin_override") is not None:
            params["admin_override"] = bool(args["admin_override"])
        return params
    if name == "workstream_release":
        params = {"workstream_id": str(args.get("workstream_id") or "")}
        if args.get("client_id"):
            params["client_id"] = str(args["client_id"])
        if args.get("lease_token"):
            params["lease_token"] = str(args["lease_token"])
        if args.get("reason"):
            params["reason"] = str(args["reason"])
        if args.get("admin_override") is not None:
            params["admin_override"] = bool(args["admin_override"])
        return params
    if name == "workstream_get":
        return {"workstream_id": str(args.get("workstream_id") or "")}
    if name == "workstream_list":
        params = {}
        if args.get("session_id"):
            params["session_id"] = str(args["session_id"])
        if args.get("client_id"):
            params["client_id"] = str(args["client_id"])
        if args.get("active_only") is not None:
            params["active_only"] = bool(args["active_only"])
        return params
    if name == "workstream_release_client":
        params = {"client_id": str(args.get("client_id") or "")}
        if args.get("reason"):
            params["reason"] = str(args["reason"])
        return params
    if name == "workstream_sweep_expired":
        return None
    if name in {"session_delegate", "session_grant_capability", "capability_revoke"}:
        return dict(args)
    if name == "tool_list":
        return _copy(args, "session_id")
    if name == "tool_show":
        return {"name": str(args.get("tool") or args.get("name") or "")}
    if name in {"tool_call", "tool_test"}:
        params: dict[str, Any] = {
            "session_id": str(args.get("session_id") or ""),
            "tool": str(args.get("tool") or ""),
        }
        if name in {"tool_call", "tool_test"}:
            params["args"] = dict(args.get("args") or {})
        return params
    if name in {"approval_show", "approval_detail"}:
        return {"id": int(args.get("id") or 0)}
    if name == "approval_approve":
        params = {
            "id": int(args.get("id") or 0),
            "decided_by": str(args.get("decided_by") or "mcp-control"),
        }
        if args.get("strong_auth"):
            params["strong_auth"] = str(args["strong_auth"])
        return params
    if name in {"approval_deny", "approval_defer"}:
        params = {
            "id": int(args.get("id") or 0),
            "decided_by": str(args.get("decided_by") or "mcp-control"),
        }
        if args.get("reason"):
            params["reason"] = str(args["reason"])
        return params
    if name == "approval_approve_group":
        params = {
            "group_id": str(args.get("group_id") or ""),
            "decided_by": str(args.get("decided_by") or "mcp-control"),
        }
        if args.get("strong_auth"):
            params["strong_auth"] = str(args["strong_auth"])
        return params
    if name.startswith("approval_pattern_"):
        return dict(args)
    if name.startswith("override_"):
        return dict(args)
    if name.startswith("relationship_group_"):
        return dict(args)
    if name.startswith("demo_"):
        return dict(args)
    if name.startswith("extract_"):
        return dict(args)
    if name.startswith("onguard_"):
        params = dict(args)
        if name in {"onguard_registry_list", "onguard_registry_register"}:
            params.setdefault("kind", "onguard")
        if name.startswith("onguard_config_") and "approved_by" not in params:
            params["approved_by"] = "mcp-control"
        if name.startswith("onguard_config_") and "proposed_by" not in params:
            params["proposed_by"] = "mcp-control"
        if name in {"onguard_events_ack"} and "acked_by" not in params:
            params["acked_by"] = "mcp-control"
        if name in {"onguard_artifact_promote"} and "promoted_by" not in params:
            params["promoted_by"] = "mcp-control"
        if name in {"onguard_schedule_run_now"} and "requested_by" not in params:
            params["requested_by"] = "mcp-control"
        if name in {"onguard_schedule_create"} and "created_by" not in params:
            params["created_by"] = "mcp-control"
        return params
    if name.startswith("programmatic_"):
        return dict(args.get("args") or args)
    if name == "gmail_configure_oauth_client":
        return {
            "client_id": str(args.get("client_id") or ""),
            "client_secret": str(args.get("client_secret") or ""),
        }
    if name == "gmail_oauth_login":
        return {
            "open_browser": bool(args.get("open_browser", True)),
            "timeout_seconds": int(args.get("timeout_seconds") or 180),
        }
    return _copy(
        args,
        "event_type",
        "event_type_contains",
        "session_id",
        "since",
        "limit",
        "state",
        "owner",
        "purpose_handle",
        "include_archived",
        "tool",
        "capability_kind",
        "args",
    )


def _copy(args: dict[str, Any], *names: str) -> dict[str, Any]:
    return {name: args[name] for name in names if name in args and args[name] is not None}


def _ok_result(result: Any) -> mcp_types.CallToolResult:
    return build_mcp_result(result, meta=_CONTROL_META, is_error=False)


def _error_result(message: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=message)],
        isError=True,
    )


async def build_control_server(client: DaemonClient) -> Server:
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return discover_control_tools()

    @server.call_tool()
    async def _call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> mcp_types.CallToolResult:
        return await dispatch_control_tool(client, name, arguments)

    return server


async def serve_control(socket_path: Path | None = None) -> None:
    socket = socket_path or default_socket_path()
    client = DaemonClient(socket)
    server = await build_control_server(client)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
