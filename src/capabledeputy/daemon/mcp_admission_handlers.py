"""Daemon RPC handlers for MCP extension admission."""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.handlers import Handler
from capabledeputy.upstream.admission import preview_server_admission
from capabledeputy.upstream.config import UpstreamServerConfig, parse_config


def make_mcp_admission_handlers(app: App) -> dict[str, Handler]:
    async def preview(params: dict[str, Any]) -> dict[str, Any]:
        config = _config_from_params(params)
        tools = params.get("tools") or []
        if not isinstance(tools, list):
            raise ValueError("mcp.admission.preview tools must be a list")
        summary = preview_server_admission(config, [dict(tool) for tool in tools])
        stored = await app.mcp_admissions.record_preview(
            summary,
            actor=str(params.get("actor") or "operator"),
        )
        await _audit(app, action="preview", server=config.name, payload=stored)
        return stored

    async def approve(params: dict[str, Any]) -> dict[str, Any]:
        server = str(params.get("server") or "")
        tools = _tool_names(params)
        result = await app.mcp_admissions.approve(
            server=server,
            tool_names=tools,
            actor=str(params.get("approved_by") or params.get("actor") or "operator"),
        )
        await _audit(app, action="approve", server=server, payload=result)
        return result

    async def disable(params: dict[str, Any]) -> dict[str, Any]:
        server = str(params.get("server") or "")
        tools = _tool_names(params)
        result = await app.mcp_admissions.disable(
            server=server,
            tool_names=tools,
            actor=str(params.get("disabled_by") or params.get("actor") or "operator"),
        )
        await _audit(app, action="disable", server=server, payload=result)
        return result

    async def list_admissions(params: dict[str, Any]) -> dict[str, Any]:
        server = str(params.get("server") or "") or None
        return await app.mcp_admissions.list(server=server)

    async def audit(params: dict[str, Any]) -> dict[str, Any]:
        server = str(params.get("server") or "") or None
        return await app.mcp_admissions.audit(
            server=server,
            limit=int(params.get("limit") or 100),
        )

    return {
        "mcp.admission.preview": preview,
        "mcp.admission.approve": approve,
        "mcp.admission.disable": disable,
        "mcp.admission.list": list_admissions,
        "mcp.admission.audit": audit,
    }


def _config_from_params(params: dict[str, Any]) -> UpstreamServerConfig:
    raw_config = params.get("config") or params.get("server_config")
    if isinstance(raw_config, dict):
        configs = parse_config({"upstream_servers": [raw_config]})
        if len(configs) != 1:
            raise ValueError("mcp.admission.preview requires exactly one server config")
        return configs[0]
    server = str(params.get("server") or "").strip()
    if not server:
        raise ValueError("mcp.admission.preview requires server or config")
    return UpstreamServerConfig(
        name=server,
        command=("admission-preview",),
        strict=bool(params.get("strict", True)),
    )


def _tool_names(params: dict[str, Any]) -> list[str]:
    raw = params.get("tools") or params.get("tool_names") or params.get("tool")
    if isinstance(raw, str):
        names = [raw]
    elif isinstance(raw, list | tuple | set | frozenset):
        names = [str(item) for item in raw]
    else:
        names = []
    names = [name.strip() for name in names if name.strip()]
    if not names:
        raise ValueError("at least one tool name is required")
    return names


async def _audit(app: App, *, action: str, server: str, payload: dict[str, Any]) -> None:
    await app.audit.write(
        Event(
            event_type=EventType.SETUP_CHANGED,
            payload={
                "action": f"mcp.admission.{action}",
                "server": server,
                "result": payload,
            },
        ),
    )
