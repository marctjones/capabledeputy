"""Daemon RPC handlers for active-context SourcePorts."""

from __future__ import annotations

from typing import Any

from capabledeputy.daemon.handlers import Handler
from capabledeputy.substrate.active_context import (
    active_context_from_payload,
    source_port_for_active_context,
)


def make_source_context_handlers() -> dict[str, Handler]:
    async def import_context(params: dict[str, Any]) -> dict[str, Any]:
        kind = str(params.get("kind") or params.get("source_kind") or "")
        payload = params.get("payload") or params
        if not isinstance(payload, dict):
            raise ValueError("source_context.import payload must be a mapping")
        return active_context_from_payload(kind, payload).to_dict()

    async def canonicalize(params: dict[str, Any]) -> dict[str, Any]:
        kind = str(params.get("kind") or params.get("source_kind") or "")
        uri = str(params.get("uri") or params.get("url") or "")
        port = source_port_for_active_context(kind)
        return {"canonical_id": port.canonicalize_resource(uri)}

    return {
        "source_context.import": import_context,
        "source_context.canonicalize": canonicalize,
    }
