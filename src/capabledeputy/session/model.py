"""Session and supporting data types (DESIGN.md §6).

`label_set` and `capability_set` use frozenset[str] as a placeholder;
Phase 2 introduces the real Label and Capability types and migrates
these fields. The Session shape itself stays stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4


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
    from_labels: frozenset[str]
    to_labels: frozenset[str]
    reason: str
    timestamp: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": str(self.audit_id),
            "from_labels": sorted(self.from_labels),
            "to_labels": sorted(self.to_labels),
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            audit_id=UUID(d["audit_id"]),
            from_labels=frozenset(d["from_labels"]),
            to_labels=frozenset(d["to_labels"]),
            reason=d["reason"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
        )


@dataclass(frozen=True)
class Session:
    id: UUID
    parent: UUID | None
    status: SessionStatus
    label_set: frozenset[str]
    capability_set: frozenset[str]
    history: tuple[Turn, ...]
    declassification_log: tuple[DeclassEvent, ...]
    created_at: datetime
    updated_at: datetime
    owner: str | None = None
    intent: str | None = None

    @classmethod
    def new(
        cls,
        *,
        parent: UUID | None = None,
        owner: str | None = None,
        intent: str | None = None,
        label_set: frozenset[str] = frozenset(),
        capability_set: frozenset[str] = frozenset(),
        history: tuple[Turn, ...] = (),
        declassification_log: tuple[DeclassEvent, ...] = (),
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
            "label_set": sorted(self.label_set),
            "capability_set": sorted(self.capability_set),
            "history": [t.to_dict() for t in self.history],
            "declassification_log": [d.to_dict() for d in self.declassification_log],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "owner": self.owner,
            "intent": self.intent,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            id=UUID(d["id"]),
            parent=UUID(d["parent"]) if d.get("parent") else None,
            status=SessionStatus(d["status"]),
            label_set=frozenset(d["label_set"]),
            capability_set=frozenset(d["capability_set"]),
            history=tuple(Turn.from_dict(t) for t in d["history"]),
            declassification_log=tuple(
                DeclassEvent.from_dict(de) for de in d["declassification_log"]
            ),
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
            owner=d.get("owner"),
            intent=d.get("intent"),
        )
