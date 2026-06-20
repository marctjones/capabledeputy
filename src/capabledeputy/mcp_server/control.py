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
    },
    required=["id"],
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
        "session_children",
        "List child sessions",
        "Return delegated child sessions for a CapDep session.",
        "session.children",
        _SESSION_ID_SCHEMA,
        _annotations("List child sessions", read_only=True, idempotent=True),
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
    if name in {"capdep_ping", "capdep_version", "daemon_info", "app_status", "setup_status"}:
        return None
    if name in {"gmail_oauth_status", "macos_frontmost_context"}:
        return None
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
    if name == "tool_list":
        return _copy(args, "session_id")
    if name in {"tool_show", "tool_call", "tool_test"}:
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
        return {
            "id": int(args.get("id") or 0),
            "decided_by": str(args.get("decided_by") or "mcp-control"),
        }
    if name in {"approval_deny", "approval_defer"}:
        params = {
            "id": int(args.get("id") or 0),
            "decided_by": str(args.get("decided_by") or "mcp-control"),
        }
        if args.get("reason"):
            params["reason"] = str(args["reason"])
        return params
    if name == "approval_approve_group":
        return {
            "group_id": str(args.get("group_id") or ""),
            "decided_by": str(args.get("decided_by") or "mcp-control"),
        }
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
    structured = result if isinstance(result, dict) else None
    text = json.dumps(result, indent=2) if isinstance(result, dict | list) else str(result)
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=False,
        **{"_meta": _CONTROL_META},
    )


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
