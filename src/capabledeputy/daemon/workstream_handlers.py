"""RPC handlers for daemon-owned interactive workstreams."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler


def make_workstream_handlers(app: App) -> dict[str, Handler]:
    async def workstream_claim(params: dict[str, Any]) -> dict[str, Any]:
        workstream = await app.workstreams.claim(
            UUID(params["session_id"]),
            str(params.get("client_id") or "interactive-client"),
            lease_seconds=int(params.get("lease_seconds") or 300),
            lease_token=params.get("lease_token"),
            reason=params.get("reason"),
            workstream_id=params.get("workstream_id"),
        )
        return {"workstream": workstream.to_dict(include_token=True)}

    async def workstream_ensure(params: dict[str, Any]) -> dict[str, Any]:
        workstream = await app.workstreams.ensure(
            UUID(params["session_id"]),
            str(params.get("client_id") or "interactive-client"),
            lease_seconds=int(params.get("lease_seconds") or 300),
            lease_token=params.get("lease_token"),
            reason=params.get("reason"),
            auto_claim=bool(params.get("auto_claim", True)),
        )
        return {"workstream": workstream.to_dict(include_token=True)}

    async def workstream_renew(params: dict[str, Any]) -> dict[str, Any]:
        workstream = await app.workstreams.renew(
            str(params["workstream_id"]),
            client_id=str(params.get("client_id") or "interactive-client"),
            lease_token=params.get("lease_token"),
            lease_seconds=int(params.get("lease_seconds") or 300),
        )
        return {"workstream": workstream.to_dict(include_token=True)}

    async def workstream_release(params: dict[str, Any]) -> dict[str, Any]:
        workstream = await app.workstreams.release(
            str(params["workstream_id"]),
            client_id=str(params.get("client_id") or "interactive-client"),
            lease_token=params.get("lease_token"),
            reason=params.get("reason"),
        )
        return {"workstream": workstream.to_dict(include_token=False)}

    async def workstream_get(params: dict[str, Any]) -> dict[str, Any]:
        workstream = await app.workstreams.get(str(params["workstream_id"]))
        return {"workstream": workstream}

    async def workstream_list(params: dict[str, Any]) -> dict[str, Any]:
        session_id = UUID(params["session_id"]) if params.get("session_id") else None
        client_id = params.get("client_id")
        return {
            "workstreams": await app.workstreams.list(
                session_id=session_id,
                client_id=str(client_id) if client_id is not None else None,
                active_only=bool(params.get("active_only", False)),
            )
        }

    return {
        "workstream.claim": workstream_claim,
        "workstream.ensure": workstream_ensure,
        "workstream.renew": workstream_renew,
        "workstream.release": workstream_release,
        "workstream.get": workstream_get,
        "workstream.list": workstream_list,
    }
