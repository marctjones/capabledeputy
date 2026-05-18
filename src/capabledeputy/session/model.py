"""Session and supporting data types (DESIGN.md §6).

Migrated in Phase 2c from frozenset[str] placeholders to frozenset[Label]
and frozenset[Capability]. The Session shape is otherwise unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label


class SessionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    ABORTED = "aborted"


_TERMINAL_STATUSES: frozenset[SessionStatus] = frozenset(
    {SessionStatus.DONE, SessionStatus.ABORTED},
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Turn:
    turn_id: int
    role: str
    content: str
    timestamp: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            turn_id=d["turn_id"],
            role=d["role"],
            content=d["content"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
        )


@dataclass(frozen=True)
class DeclassEvent:
    audit_id: UUID
    from_labels: frozenset[Label]
    to_labels: frozenset[Label]
    reason: str
    timestamp: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": str(self.audit_id),
            "from_labels": sorted(label.value for label in self.from_labels),
            "to_labels": sorted(label.value for label in self.to_labels),
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            audit_id=UUID(d["audit_id"]),
            from_labels=frozenset(Label(s) for s in d["from_labels"]),
            to_labels=frozenset(Label(s) for s in d["to_labels"]),
            reason=d["reason"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
        )


@dataclass(frozen=True)
class Session:
    id: UUID
    parent: UUID | None
    status: SessionStatus
    label_set: frozenset[Label]
    capability_set: frozenset[Capability]
    history: tuple[Turn, ...]
    declassification_log: tuple[DeclassEvent, ...]
    created_at: datetime
    updated_at: datetime
    owner: str | None = None
    intent: str | None = None
    tool_aliasing: bool = False
    prefer_programmatic: bool = False
    used_kinds: frozenset[CapabilityKind] = field(default_factory=frozenset)
    # Per-capability use timestamps for sliding-window rate limiting,
    # keyed by capability audit_id. Treated immutably (replace()), like
    # used_kinds. Empty ⇒ nothing rate-limited yet.
    cap_uses: dict[str, tuple[datetime, ...]] = field(default_factory=dict)
    # 002 delegation cascade: capability audit_ids explicitly revoked
    # in/for this session's authority graph. Consulted at decide();
    # additive, default-tolerant on read (missing ⇒ empty).
    revoked_audit_ids: frozenset[UUID] = field(default_factory=frozenset)

    @classmethod
    def new(
        cls,
        *,
        parent: UUID | None = None,
        owner: str | None = None,
        intent: str | None = None,
        label_set: frozenset[Label] = frozenset(),
        capability_set: frozenset[Capability] = frozenset(),
        history: tuple[Turn, ...] = (),
        declassification_log: tuple[DeclassEvent, ...] = (),
        tool_aliasing: bool = False,
        prefer_programmatic: bool = False,
        used_kinds: frozenset[CapabilityKind] = frozenset(),
        cap_uses: dict[str, tuple[datetime, ...]] | None = None,
        revoked_audit_ids: frozenset[UUID] = frozenset(),
    ) -> Self:
        now = _utcnow()
        return cls(
            id=uuid4(),
            parent=parent,
            status=SessionStatus.ACTIVE,
            label_set=label_set,
            capability_set=capability_set,
            history=history,
            declassification_log=declassification_log,
            created_at=now,
            updated_at=now,
            owner=owner,
            intent=intent,
            tool_aliasing=tool_aliasing,
            prefer_programmatic=prefer_programmatic,
            used_kinds=used_kinds,
            cap_uses=cap_uses if cap_uses is not None else {},
            revoked_audit_ids=revoked_audit_ids,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def with_status(self, status: SessionStatus) -> Self:
        return replace(self, status=status, updated_at=_utcnow())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "parent": str(self.parent) if self.parent else None,
            "status": self.status.value,
            "label_set": sorted(label.value for label in self.label_set),
            "capability_set": sorted(
                (c.to_dict() for c in self.capability_set),
                key=lambda d: d["audit_id"],
            ),
            "history": [t.to_dict() for t in self.history],
            "declassification_log": [d.to_dict() for d in self.declassification_log],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "owner": self.owner,
            "intent": self.intent,
            "tool_aliasing": self.tool_aliasing,
            "prefer_programmatic": self.prefer_programmatic,
            "used_kinds": sorted(k.value for k in self.used_kinds),
            "cap_uses": {
                aid: [ts.isoformat() for ts in stamps] for aid, stamps in self.cap_uses.items()
            },
            "revoked_audit_ids": sorted(str(a) for a in self.revoked_audit_ids),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            id=UUID(d["id"]),
            parent=UUID(d["parent"]) if d.get("parent") else None,
            status=SessionStatus(d["status"]),
            label_set=frozenset(Label(s) for s in d["label_set"]),
            capability_set=frozenset(Capability.from_dict(c) for c in d["capability_set"]),
            history=tuple(Turn.from_dict(t) for t in d["history"]),
            declassification_log=tuple(
                DeclassEvent.from_dict(de) for de in d["declassification_log"]
            ),
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
            owner=d.get("owner"),
            intent=d.get("intent"),
            tool_aliasing=bool(d.get("tool_aliasing", False)),
            prefer_programmatic=bool(d.get("prefer_programmatic", False)),
            used_kinds=frozenset(CapabilityKind(k) for k in d.get("used_kinds", ())),
            cap_uses={
                aid: tuple(datetime.fromisoformat(ts) for ts in stamps)
                for aid, stamps in d.get("cap_uses", {}).items()
            },
            revoked_audit_ids=frozenset(UUID(a) for a in d.get("revoked_audit_ids", ())),
        )
