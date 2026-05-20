"""T067 — Override Grant is distinct from ordinary approval (FR-038).

The capability produced by `use_override` carries
`origin == OVERRIDE_GRANTED`, never `USER_APPROVED`. The audit
object is structurally separate (different event types, different
store conventions). These tests pin the distinction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.overrides import (
    FrictionLevel,
    GrantState,
    HardFloor,
    OverrideGrant,
    OverridePolicy,
    OverridePolicyEntry,
    OverrideRefusal,
    OverrideRefusalReason,
    use_override,
)


def _active_grant(
    *,
    session_id=None,
    expires_at: datetime | None = None,
) -> OverrideGrant:
    return OverrideGrant(
        id=uuid4(),
        session_id=session_id or uuid4(),
        action_kind=CapabilityKind.SEND_EMAIL,
        target="alice@example.com",
        target_category_tier=("personal", "restricted"),
        hard_floor_crossed=HardFloor.MAX_TIER_CLEARANCE,
        invoker_principal="alice",
        attester_principal=None,
        policy_at_grant=OverridePolicyEntry(
            floor=HardFloor.MAX_TIER_CLEARANCE,
            policy=OverridePolicy.SINGLE_AUTHORIZED,
            authorized_principal_ids=frozenset({"alice"}),
        ),
        friction_level=FrictionLevel.MEDIUM,
        state=GrantState.ACTIVE,
        expires_at=expires_at or datetime.now(UTC) + timedelta(seconds=60),
    )


def test_use_grant_mints_override_granted_capability() -> None:
    """The capability returned by use_override carries
    OVERRIDE_GRANTED, never USER_APPROVED (FR-038)."""
    grant = _active_grant()
    cap = use_override(
        grant,
        action_kind=CapabilityKind.SEND_EMAIL,
        target="alice@example.com",
    )
    assert isinstance(cap, Capability)
    assert cap.origin == CapabilityOrigin.OVERRIDE_GRANTED
    assert cap.origin != CapabilityOrigin.USER_APPROVED
    assert cap.override_grant_id == grant.id


def test_use_grant_carries_expiry_from_grant() -> None:
    """The minted capability inherits the grant's expires_at — no
    perpetual override-derived capability."""
    now = datetime(2026, 5, 19, 11, 0, tzinfo=UTC)
    expires = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    grant = _active_grant(expires_at=expires)
    cap = use_override(
        grant,
        action_kind=CapabilityKind.SEND_EMAIL,
        target="alice@example.com",
        now=now,
    )
    assert isinstance(cap, Capability)
    assert cap.expires_at == expires


def test_use_grant_action_mismatch_refused() -> None:
    """A grant binds to (action_kind, target). Different action ⇒
    refused with ACTION_MISMATCH."""
    grant = _active_grant()
    result = use_override(
        grant,
        action_kind=CapabilityKind.READ_FS,
        target="alice@example.com",
    )
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.ACTION_MISMATCH


def test_expired_grant_use_refused() -> None:
    past = datetime(2020, 1, 1, tzinfo=UTC)
    grant = _active_grant(expires_at=past)
    result = use_override(
        grant,
        action_kind=CapabilityKind.SEND_EMAIL,
        target="alice@example.com",
    )
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.GRANT_EXPIRED


def test_override_granted_capability_roundtrips_through_to_from_dict() -> None:
    """The override_grant_id field round-trips through serialization
    so persisted grants stay traceable."""
    grant = _active_grant()
    cap = use_override(
        grant,
        action_kind=CapabilityKind.SEND_EMAIL,
        target="alice@example.com",
    )
    assert isinstance(cap, Capability)
    payload = cap.to_dict()
    assert payload["origin"] == "override_granted"
    assert payload["override_grant_id"] == str(grant.id)
    rehydrated = Capability.from_dict(payload)
    assert rehydrated.origin == CapabilityOrigin.OVERRIDE_GRANTED
    assert rehydrated.override_grant_id == grant.id
