"""Session and supporting data types (DESIGN.md §6).

DeclassEvent stores label strings; capabilities use four-axis LabelState for
composition and security reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import AxisD, LabelState


class SessionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    ABORTED = "aborted"


class EnforcementMode(StrEnum):
    """Per-session enforcement posture (cookbook Pattern ⑥).

    STRICT (default) — every decide() result fires as authored.
    DENY blocks, SUGGEST/REQUIRE_APPROVAL routes to the approval
    queue, AUTO proceeds. Production behavior; back-compat with
    every pre-Pattern-⑥ session.

    SHADOW — the engine still computes the decision normally, but
    non-ALLOW outcomes are REWRITTEN to ALLOW for the dispatcher
    while a POLICY_SHADOWED audit event records what would have
    happened. Capability checks are NOT bypassed (a missing
    capability still denies — that's a structural check, not a
    rule outcome). Operator uses SHADOW for K turns of new-rule
    validation, reviews the audit log, then flips to STRICT.

    The mode is a per-session attribute, mutable via /enforce
    in the chat REPL or session.set_enforcement RPC. Toggling
    emits its own audit event so the log answers "what was the
    enforcement posture when this decision fired?" deterministically.
    """

    STRICT = "strict"
    SHADOW = "shadow"


@dataclass(frozen=True)
class OriginMetadata:
    """Structured actor metadata for multi-client and onguard sessions.

    The daemon stores this on the session so policy/Starlark/audit can reason
    about whether work came from a foreground human, MCP-control host, upstream
    MCP bridge, scheduled onguard client, queued worker, or system-internal
    maintenance path.
    """

    kind: str = "human_interactive"
    client_id: str | None = None
    schedule_id: str | None = None
    command_id: str | None = None
    proposed_by: str | None = None
    approved_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "client_id": self.client_id,
            "schedule_id": self.schedule_id,
            "command_id": self.command_id,
            "proposed_by": self.proposed_by,
            "approved_by": self.approved_by,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Self:
        raw = d or {}
        return cls(
            kind=str(raw.get("kind") or "human_interactive"),
            client_id=raw.get("client_id"),
            schedule_id=raw.get("schedule_id"),
            command_id=raw.get("command_id"),
            proposed_by=raw.get("proposed_by"),
            approved_by=raw.get("approved_by"),
            metadata=dict(raw.get("metadata") or {}),
        )


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
            from_labels=frozenset(str(s) for s in d["from_labels"]),
            to_labels=frozenset(str(s) for s in d["to_labels"]),
            reason=d["reason"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
        )


@dataclass(frozen=True)
class Session:
    id: UUID
    parent: UUID | None
    status: SessionStatus
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
    # Defaults are safe: empty state + 'unset' purpose ⇒ admits no
    # consequential effects (FR-046 fail-closed at decide()).
    # R4b.4: collapsed axis_a + axis_b into single label_state field.
    label_state: LabelState = field(default_factory=LabelState)
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
    # Cookbook Pattern ⑥ — per-session enforcement posture.
    # Default STRICT so back-compat with every pre-Pattern-⑥
    # session and every test fixture is preserved. SHADOW is the
    # operator-opt-in mode for new-rule validation.
    enforcement_mode: EnforcementMode = EnforcementMode.STRICT
    origin: OriginMetadata = field(default_factory=OriginMetadata)
    # Cookbook §4 #6 — first-action-of-kind prompt. When True, the
    # engine returns SUGGEST instead of ALLOW the FIRST time this
    # session exercises any promptable capability kind (sends,
    # purchases, destructive ops, sandbox/devbox execution). After
    # the operator approves, the kind enters `used_kinds` and
    # subsequent dispatches pass through normally. Default False
    # for back-compat — sessions opt in via the Purpose template
    # (cautious dial → True) or the session.set_first_use_prompts
    # RPC.
    first_use_prompt_enabled: bool = False

    @classmethod
    def new(
        cls,
        *,
        parent: UUID | None = None,
        owner: str | None = None,
        intent: str | None = None,
        capability_set: frozenset[Capability] = frozenset(),
        history: tuple[Turn, ...] = (),
        declassification_log: tuple[DeclassEvent, ...] = (),
        tool_aliasing: bool = False,
        prefer_programmatic: bool = False,
        used_kinds: frozenset[CapabilityKind] = frozenset(),
        cap_uses: dict[str, tuple[datetime, ...]] | None = None,
        revoked_audit_ids: frozenset[UUID] = frozenset(),
        label_state: LabelState | None = None,
        axis_d: AxisD | None = None,
        purpose_handle: str = "unset",
        reference_handles: dict[str, dict[str, Any]] | None = None,
        risk_preference_at_spawn: str = "cautious",
        effective_isolation_region_id: str | None = None,
        clearance_profile_id: str | None = None,
        first_use_prompt_enabled: bool = False,
        origin: OriginMetadata | dict[str, Any] | None = None,
    ) -> Self:
        now = _utcnow()
        origin_metadata = (
            origin if isinstance(origin, OriginMetadata) else OriginMetadata.from_dict(origin)
        )
        return cls(
            id=uuid4(),
            parent=parent,
            status=SessionStatus.ACTIVE,
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
            label_state=label_state if label_state is not None else LabelState(),
            axis_d=axis_d if axis_d is not None else AxisD(),
            purpose_handle=purpose_handle,
            reference_handles=reference_handles if reference_handles is not None else {},
            risk_preference_at_spawn=risk_preference_at_spawn,
            effective_isolation_region_id=effective_isolation_region_id,
            clearance_profile_id=clearance_profile_id,
            first_use_prompt_enabled=first_use_prompt_enabled,
            origin=origin_metadata,
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
            # R4b.4: collapsed axis_a + axis_b into label_state.
            "label_state": self.label_state.to_dict(),
            "axis_d": self.axis_d.to_dict(),
            "purpose_handle": self.purpose_handle,
            "reference_handles": self.reference_handles,
            "risk_preference_at_spawn": self.risk_preference_at_spawn,
            "effective_isolation_region_id": self.effective_isolation_region_id,
            "clearance_profile_id": self.clearance_profile_id,
            "enforcement_mode": self.enforcement_mode.value,
            "first_use_prompt_enabled": self.first_use_prompt_enabled,
            "origin": self.origin.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            id=UUID(d["id"]),
            parent=UUID(d["parent"]) if d.get("parent") else None,
            status=SessionStatus(d["status"]),
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
            # R4b.4: collapsed axis_a + axis_b into label_state.
            label_state=LabelState.from_dict(d.get("label_state")),
            axis_d=AxisD.from_dict(d.get("axis_d")),
            purpose_handle=str(d.get("purpose_handle", "unset")),
            reference_handles=dict(d.get("reference_handles") or {}),
            risk_preference_at_spawn=str(d.get("risk_preference_at_spawn", "cautious")),
            effective_isolation_region_id=d.get("effective_isolation_region_id"),
            clearance_profile_id=d.get("clearance_profile_id"),
            # Default-tolerant on read so pre-Pattern-⑥ sessions
            # in the state DB load as STRICT (current behavior).
            enforcement_mode=EnforcementMode(
                d.get("enforcement_mode", EnforcementMode.STRICT.value),
            ),
            first_use_prompt_enabled=bool(
                d.get("first_use_prompt_enabled", False),
            ),
            origin=OriginMetadata.from_dict(d.get("origin")),
        )
