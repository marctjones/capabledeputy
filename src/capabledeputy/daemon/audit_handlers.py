"""RPC handlers for audit log queries (DESIGN.md §9.3)."""

from __future__ import annotations

from typing import Any

from capabledeputy.audit.events import Event
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.daemon.handlers import Handler


def make_audit_handlers(audit: AuditWriter) -> dict[str, Handler]:
    async def audit_tail(params: dict[str, Any]) -> dict[str, Any]:
        after = params.get("after_audit_id")
        limit = int(params.get("limit", 100))
        events = await audit.tail(after_audit_id=after, limit=limit)
        return {"events": [e.to_dict() for e in events]}

    async def audit_list(params: dict[str, Any]) -> dict[str, Any]:
        events = await audit.read_all()
        filtered = _filter(events, params)
        limit = int(params.get("limit", 100))
        return {"events": [e.to_dict() for e in filtered[-limit:]]}

    return {
        "audit.list": audit_list,
        "audit.tail": audit_tail,
    }


def _filter(events: list[Event], params: dict[str, Any]) -> list[Event]:
    event_type = params.get("event_type")
    session_id = params.get("session_id")

    if event_type is not None:
        events = [e for e in events if e.event_type.value == event_type]
    if session_id is not None:
        sid = str(session_id)
        events = [e for e in events if e.session_id and str(e.session_id) == sid]
    return events
