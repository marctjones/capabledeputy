"""Audit event taxonomy.

Defines the canonical wire format for the audit log (DESIGN.md §9.2).
Every event in the log is one of these types, serialized to JSONL.
The full taxonomy is wired in from Phase 1 even though most emitters
arrive in later phases — retrofitting the trace shape is painful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4


class EventType(StrEnum):
    SESSION_CREATED = "session.created"
    SESSION_FORKED = "session.forked"
    SESSION_PAUSED = "session.paused"
    SESSION_RESUMED = "session.resumed"
    SESSION_MERGED = "session.merged"
    SESSION_ABORTED = "session.aborted"
    SESSION_DONE = "session.done"

    LLM_CONTEXT_ASSEMBLED = "llm.context_assembled"
    LLM_REQUEST_SENT = "llm.request_sent"
    LLM_RESPONSE_RECEIVED = "llm.response_received"
    LLM_RESPONSE_PARSED = "llm.response_parsed"

    MODE_SELECTED = "mode.selected"
    POLICY_DECIDED = "policy.decided"

    LABEL_PROPAGATED = "label.propagated"
    CAPABILITY_CHECKED = "capability.checked"

    TOOL_DISPATCHED = "tool.dispatched"
    TOOL_RETURNED = "tool.returned"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_DENIED = "approval.denied"
    APPROVAL_DEFERRED = "approval.deferred"
    APPROVAL_EXPIRED = "approval.expired"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Event:
    event_type: EventType
    session_id: UUID | None = None
    turn_id: int | None = None
    step_id: int | None = None
    timestamp: datetime = field(default_factory=_utcnow)
    payload: dict[str, Any] = field(default_factory=dict)
    audit_id: UUID = field(default_factory=uuid4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": str(self.audit_id),
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "session_id": str(self.session_id) if self.session_id else None,
            "turn_id": self.turn_id,
            "step_id": self.step_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            audit_id=UUID(d["audit_id"]),
            timestamp=datetime.fromisoformat(d["timestamp"]),
            event_type=EventType(d["event_type"]),
            session_id=UUID(d["session_id"]) if d.get("session_id") else None,
            turn_id=d.get("turn_id"),
            step_id=d.get("step_id"),
            payload=d.get("payload") or {},
        )
