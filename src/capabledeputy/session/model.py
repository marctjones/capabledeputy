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
from capabledeputy.policy.labels import AxisA, AxisB, AxisD, Label


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
    # 003 v0.9 labeling — T010. Four-axis representation. axis_c lives
    # on Capability.kind / ToolDefinition.effect_class, not here.
    # Defaults are safe: empty axes + 'unset' purpose ⇒ admits no
    # consequential effects (FR-046 fail-closed at decide()).
    axis_a: AxisA = field(default_factory=AxisA)
    axis_b: AxisB = field(default_factory=AxisB)
    axis_d: AxisD = field(default_factory=AxisD)
    purpose_handle: str = "unset"
    reference_handles: dict[str, dict[str, Any]] = field(default_factory=dict)
    risk_preference_at_spawn: str = "cautious"
    effective_isolation_region_id: str | None = None
    # 003 runtime activation — the profile id under which this
    # session is being evaluated. PolicyContext's loaded profiles
    # registry resolves it; the engine derives clearance_max_tier
    # and integrity_floor_level from the profile (FR-008 / FR-004).
    clearance_profile_id: str | None = None

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
        axis_a: AxisA | None = None,
        axis_b: AxisB | None = None,
        axis_d: AxisD | None = None,
        purpose_handle: str = "unset",
        reference_handles: dict[str, dict[str, Any]] | None = None,
        risk_preference_at_spawn: str = "cautious",
        effective_isolation_region_id: str | None = None,
        clearance_profile_id: str | None = None,
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
            axis_a=axis_a if axis_a is not None else AxisA(),
            axis_b=axis_b if axis_b is not None else AxisB(),
            axis_d=axis_d if axis_d is not None else AxisD(),
            purpose_handle=purpose_handle,
            reference_handles=reference_handles if reference_handles is not None else {},
            risk_preference_at_spawn=risk_preference_at_spawn,
            effective_isolation_region_id=effective_isolation_region_id,
            clearance_profile_id=clearance_profile_id,
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
            # 003 v0.9 four-axis additions (T010). axis_c lives on caps.
            "axis_a": self.axis_a.to_dict(),
            "axis_b": self.axis_b.to_dict(),
            "axis_d": self.axis_d.to_dict(),
            "purpose_handle": self.purpose_handle,
            "reference_handles": self.reference_handles,
            "risk_preference_at_spawn": self.risk_preference_at_spawn,
            "effective_isolation_region_id": self.effective_isolation_region_id,
            "clearance_profile_id": self.clearance_profile_id,
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
            # 003 v0.9 four-axis additions — default-tolerant per
            # Constitution §Sec. Constraints (T010 / FR-045).
            axis_a=AxisA.from_dict(d.get("axis_a") or []),
            axis_b=AxisB.from_dict(d.get("axis_b") or []),
            axis_d=AxisD.from_dict(d.get("axis_d")),
            purpose_handle=str(d.get("purpose_handle", "unset")),
            reference_handles=dict(d.get("reference_handles") or {}),
            risk_preference_at_spawn=str(d.get("risk_preference_at_spawn", "cautious")),
            effective_isolation_region_id=d.get("effective_isolation_region_id"),
            clearance_profile_id=d.get("clearance_profile_id"),
        )
