"""ApprovalRequest data model (DESIGN.md §8.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from capabledeputy.policy.capabilities import Capability
from capabledeputy.policy.labels import Label


class ApprovalAction(StrEnum):
    DECLASSIFY = "DECLASSIFY"
    SEND_EMAIL = "SEND_EMAIL"
    MERGE = "MERGE"
    GRANT = "GRANT"
    QUEUE_PURCHASE = "QUEUE_PURCHASE"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    DEFERRED = "deferred"
    EXPIRED = "expired"


@dataclass(frozen=True)
class ApprovalRequest:
    id: int
    audit_id: UUID
    from_session: UUID
    action: ApprovalAction
    payload: str
    target: str
    labels_in: frozenset[Label]
    labels_out: frozenset[Label]
    capability_requested: Capability | None
    justification: str
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: ApprovalStatus = ApprovalStatus.PENDING
    to_session: UUID | None = None
    decision_at: datetime | None = None
    decided_by: str | None = None
    decision_scope: dict[str, Any] = field(default_factory=dict)
    new_audit_id: UUID = field(default_factory=uuid4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "audit_id": str(self.audit_id),
            "from_session": str(self.from_session),
            "to_session": str(self.to_session) if self.to_session else None,
            "action": self.action.value,
            "payload": self.payload,
            "target": self.target,
            "labels_in": sorted(label.value for label in self.labels_in),
            "labels_out": sorted(label.value for label in self.labels_out),
            "capability_requested": (
                self.capability_requested.to_dict() if self.capability_requested else None
            ),
            "justification": self.justification,
            "requested_at": self.requested_at.isoformat(),
            "status": self.status.value,
            "decision_at": (self.decision_at.isoformat() if self.decision_at else None),
            "decided_by": self.decided_by,
            "decision_scope": self.decision_scope,
        }
