"""ApprovalRequest data model (DESIGN.md §8.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from capabledeputy.policy.capabilities import Capability
from capabledeputy.policy.labels import LabelState


class ApprovalAction(StrEnum):
    DECLASSIFY = "DECLASSIFY"
    ELICITATION = "ELICITATION"
    SEND_EMAIL = "SEND_EMAIL"
    MERGE = "MERGE"
    GRANT = "GRANT"
    QUEUE_PURCHASE = "QUEUE_PURCHASE"
    # Generic gate for destructive-op-needs-approval. Payload encodes
    # the tool + args as JSON so a single executor can dispatch any
    # MODIFY_* or DELETE_* tool through a purpose-limited session.
    EXECUTE_DESTRUCTIVE = "EXECUTE_DESTRUCTIVE"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    DEFERRED = "deferred"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class ApprovalRequest:
    id: int
    audit_id: UUID
    from_session: UUID
    action: ApprovalAction
    payload: str
    target: str
    labels_in: LabelState
    labels_out: LabelState
    capability_requested: Capability | None
    justification: str
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: ApprovalStatus = ApprovalStatus.PENDING
    to_session: UUID | None = None
    decision_at: datetime | None = None
    decided_by: str | None = None
    decision_scope: dict[str, Any] = field(default_factory=dict)
    new_audit_id: UUID = field(default_factory=uuid4)
    # Cookbook P2.1 — sibling grouping. Two pending requests within a
    # short window with the same (session, action, target) carry the
    # same sibling_group_id, so the approval UI can render one card
    # with `approve-all + per-item toggles` instead of N separate
    # cards. None when the request stands alone.
    sibling_group_id: UUID | None = None
    # Cookbook P2.7 — stale-approval TTL. PENDING requests that pass
    # their expires_at flip to EXPIRED on the next queue access and
    # emit APPROVAL_EXPIRED. None ⇒ no TTL (legacy / explicit
    # immortal). Computed at submit time from the queue's
    # default_ttl_seconds OR the caller's explicit ttl_seconds; the
    # caller passing ttl_seconds=0 produces None (never expires).
    expires_at: datetime | None = None
    # Roadmap v2 #7 — the engine rule that triggered the gate (e.g.
    # `first-use-of-kind`, `untrusted-meets-egress`, or the operator
    # rule that escalated to REQUIRE_APPROVAL). Lets the REPL render
    # mechanism-specific cards: a first-use card looks different from
    # a rule-driven approval, since the question being asked of the
    # operator is fundamentally different ("intend to ever do this?"
    # vs "this matches the rule you wrote — proceed?"). None when the
    # caller didn't supply a rule (legacy / out-of-band approvals).
    rule: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ApprovalRequest:
        """Read a daemon-owned review record; malformed records fail closed."""
        return cls(
            id=int(raw["id"]),
            audit_id=UUID(raw["audit_id"]),
            from_session=UUID(raw["from_session"]),
            action=ApprovalAction(raw["action"]),
            payload=raw["payload"],
            target=raw["target"],
            labels_in=LabelState.from_dict(raw["labels_in"]),
            labels_out=LabelState.from_dict(raw["labels_out"]),
            capability_requested=(
                Capability.from_dict(raw["capability_requested"])
                if raw["capability_requested"]
                else None
            ),
            justification=raw["justification"],
            requested_at=datetime.fromisoformat(raw["requested_at"]),
            status=ApprovalStatus(raw["status"]),
            to_session=UUID(raw["to_session"]) if raw.get("to_session") else None,
            decision_at=datetime.fromisoformat(raw["decision_at"]) if raw["decision_at"] else None,
            decided_by=raw["decided_by"],
            decision_scope=raw["decision_scope"],
            sibling_group_id=UUID(raw["sibling_group_id"]) if raw["sibling_group_id"] else None,
            expires_at=datetime.fromisoformat(raw["expires_at"]) if raw["expires_at"] else None,
            rule=raw["rule"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "audit_id": str(self.audit_id),
            "from_session": str(self.from_session),
            "to_session": str(self.to_session) if self.to_session else None,
            "action": self.action.value,
            "payload": self.payload,
            "target": self.target,
            "labels_in": self.labels_in.to_dict(),
            "labels_out": self.labels_out.to_dict(),
            "capability_requested": (
                self.capability_requested.to_dict() if self.capability_requested else None
            ),
            "justification": self.justification,
            "requested_at": self.requested_at.isoformat(),
            "status": self.status.value,
            "decision_at": (self.decision_at.isoformat() if self.decision_at else None),
            "decided_by": self.decided_by,
            "decision_scope": self.decision_scope,
            "sibling_group_id": (
                str(self.sibling_group_id) if self.sibling_group_id is not None else None
            ),
            "expires_at": (self.expires_at.isoformat() if self.expires_at is not None else None),
            "rule": self.rule,
        }
