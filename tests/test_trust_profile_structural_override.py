"""Slice B — structural conflict floors are override-targetable.

The four always-on information-flow conflict invariants (untrusted/health/
financial co-presence with egress) DENY by default. Slice B makes them
*mintable* as override floors so that, under the `personal` trust profile,
the operator can solo-override them on their own initiative — while `managed`
keeps them as hard floors (no override path).

The engine needs no change: the grant short-circuit is floor-agnostic and
runs before the conflict invariants, so an active grant for (session, kind,
target) already crosses the DENY. These tests prove the end-to-end behavior
and the safety properties the design pins:

  - personal: a solo grant crosses health-meets-egress; managed cannot mint one.
  - untrusted-egress: the grant is pinned to an EXACT destination — untrusted
    content cannot redirect the flow to a different target (Pattern ③).
  - friction scales: the gravest structural floors require MAXIMAL friction.
"""

from __future__ import annotations

from uuid import uuid4

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.engine import decide
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.overrides import (
    FrictionLevel,
    HardFloor,
    OverrideGrant,
    OverrideGrantStore,
    OverridePolicies,
    OverrideRefusal,
    OverrideRefusalReason,
    TrustProfile,
    request_override,
    structural_floor_for_rule,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier

_HEALTH = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))
_UNTRUSTED = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
# A matching capability so the BASE decision would ALLOW — only then does the
# conflict invariant become the deciding rule (otherwise no-capability DENY
# wins first and masks the floor we're exercising).
_EMAIL_CAP = frozenset(
    {
        Capability(
            kind=CapabilityKind.SEND_EMAIL,
            pattern="*",
            origin=CapabilityOrigin.USER_APPROVED,
        ),
    },
)


def _personal() -> OverridePolicies:
    return OverridePolicies(
        by_floor={},
        trust_profile=TrustProfile.PERSONAL,
        operator_principal="owner",
    )


def _mint(
    policies: OverridePolicies,
    *,
    session_id,
    floor: HardFloor,
    target: str,
    invoker: str = "owner",
):
    return request_override(
        policies=policies,
        session_id=session_id,
        action_kind=CapabilityKind.SEND_EMAIL,
        target=target,
        target_category_tier=("health", "restricted"),
        floor=floor,
        invoker=invoker,
        friction_confirmed=True,
    )


# --- the rule ↔ floor identity map ----------------------------------


def test_structural_floor_for_rule_maps_conflict_rules() -> None:
    assert structural_floor_for_rule("health-meets-egress") is HardFloor.HEALTH_EGRESS
    assert structural_floor_for_rule("untrusted-meets-egress") is HardFloor.PROVENANCE_EGRESS
    assert structural_floor_for_rule("financial-meets-email") is HardFloor.FINANCIAL_EMAIL
    assert structural_floor_for_rule("financial-meets-purchase") is HardFloor.FINANCIAL_PURCHASE


def test_structural_floor_for_rule_rejects_non_structural() -> None:
    # The FR-026d hard floors are not structural; arbitrary rules are not floors.
    assert structural_floor_for_rule("prohibited") is None
    assert structural_floor_for_rule("override-grant-active") is None
    assert not HardFloor.PROHIBITED.is_structural
    assert HardFloor.HEALTH_EGRESS.is_structural


# --- baseline: the conflict invariant denies without a grant --------


def test_health_egress_denies_without_grant() -> None:
    """Sanity floor: a health-tainted session sending email DENIES on
    health-meets-egress (the invariant we are making override-targetable)."""
    result = decide(
        _EMAIL_CAP,
        Action(kind=CapabilityKind.SEND_EMAIL, target="doctor@example.com"),
        labels=_HEALTH,
        session_id=uuid4(),
    )
    assert result.decision == Decision.DENY
    assert result.rule == "health-meets-egress"


# --- personal: solo operator override crosses the structural floor --


def test_personal_solo_override_crosses_health_egress() -> None:
    """The operator mints a solo grant naming the health-egress floor; the
    engine short-circuit crosses the otherwise-DENY health-meets-egress."""
    sid = uuid4()
    grant = _mint(
        _personal(),
        session_id=sid,
        floor=HardFloor.HEALTH_EGRESS,
        target="doctor@example.com",
    )
    assert isinstance(grant, OverrideGrant)
    store = OverrideGrantStore()
    store.add(grant)
    result = decide(
        _EMAIL_CAP,
        Action(kind=CapabilityKind.SEND_EMAIL, target="doctor@example.com"),
        labels=_HEALTH,
        override_grants=store,
        session_id=sid,
    )
    assert result.decision == Decision.ALLOW
    assert result.rule == "override-grant-active"


def test_managed_cannot_mint_structural_floor_override() -> None:
    """Under `managed`, a structural floor has no override default — the
    request refuses (POLICY_DISALLOWED), so the structural DENY holds. The
    operator-root power is gated by the trust profile."""
    managed = OverridePolicies(by_floor={}, trust_profile=TrustProfile.MANAGED)
    refusal = _mint(
        managed,
        session_id=uuid4(),
        floor=HardFloor.HEALTH_EGRESS,
        target="doctor@example.com",
    )
    assert isinstance(refusal, OverrideRefusal)
    assert refusal.reason == OverrideRefusalReason.POLICY_DISALLOWED


# --- untrusted-egress: pinned destination (redirection-resistance) --


def test_untrusted_override_is_pinned_to_destination() -> None:
    """The operator overrides untrusted-egress for ONE destination. Untrusted
    content cannot redirect the flow: a send to a different (injected) target
    is NOT covered by the grant — untrusted-meets-egress still DENIES — while
    the operator-pinned destination is allowed. (Pattern ③ / the design's
    'untrusted content can never redirect' invariant.)"""
    sid = uuid4()
    grant = _mint(
        _personal(),
        session_id=sid,
        floor=HardFloor.PROVENANCE_EGRESS,
        target="doctor@example.com",  # operator-pinned, human-typed
    )
    assert isinstance(grant, OverrideGrant)
    store = OverrideGrantStore()
    store.add(grant)

    # Injected redirect to a different target — grant does not match, the
    # untrusted floor holds. (Mismatch does not consume the grant.)
    redirected = decide(
        _EMAIL_CAP,
        Action(kind=CapabilityKind.SEND_EMAIL, target="attacker@evil.example"),
        labels=_UNTRUSTED,
        override_grants=store,
        session_id=sid,
    )
    assert redirected.decision == Decision.DENY
    assert redirected.rule == "untrusted-meets-egress"

    # The operator-pinned destination is the only thing the grant authorizes.
    pinned = decide(
        _EMAIL_CAP,
        Action(kind=CapabilityKind.SEND_EMAIL, target="doctor@example.com"),
        labels=_UNTRUSTED,
        override_grants=store,
        session_id=sid,
    )
    assert pinned.decision == Decision.ALLOW
    assert pinned.rule == "override-grant-active"


# --- friction scales with severity ----------------------------------


def test_untrusted_and_health_floors_require_maximal_friction() -> None:
    sid = uuid4()
    for floor in (HardFloor.PROVENANCE_EGRESS, HardFloor.HEALTH_EGRESS):
        grant = _mint(_personal(), session_id=sid, floor=floor, target="x@example.com")
        assert isinstance(grant, OverrideGrant)
        assert grant.friction_level == FrictionLevel.MAXIMAL, floor
