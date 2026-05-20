"""T066 — Override policy FSM (FR-036 / SC-014 / SC-011 / Quickstart §4).

Three operator-declared policies: `disallowed`, `single-authorized`,
`dual-control`. Each governs a hard floor (`prohibited`,
admissibility-exclusion, max-tier-clearance, integrity-floor).
The FSM:
  - rejects unauthorized invokers,
  - requires distinct attesters for dual-control,
  - refuses friction-not-met,
  - issues grants with non-null expiry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.overrides import (
    FrictionLevel,
    GrantState,
    HardFloor,
    OverrideGrant,
    OverridePolicies,
    OverridePolicy,
    OverridePolicyEntry,
    OverrideRefusal,
    OverrideRefusalReason,
    attest_override,
    request_override,
)


def _policies() -> OverridePolicies:
    return OverridePolicies(
        by_floor={
            HardFloor.PROHIBITED: OverridePolicyEntry(
                floor=HardFloor.PROHIBITED,
                policy=OverridePolicy.DISALLOWED,
            ),
            HardFloor.ADMISSIBILITY_EXCLUSION: OverridePolicyEntry(
                floor=HardFloor.ADMISSIBILITY_EXCLUSION,
                policy=OverridePolicy.DUAL_CONTROL,
                authorized_principal_ids=frozenset({"alice"}),
                attester_principal_ids=frozenset({"bob", "carol"}),
                expiry_seconds=120,
            ),
            HardFloor.MAX_TIER_CLEARANCE: OverridePolicyEntry(
                floor=HardFloor.MAX_TIER_CLEARANCE,
                policy=OverridePolicy.SINGLE_AUTHORIZED,
                authorized_principal_ids=frozenset({"alice"}),
                expiry_seconds=60,
            ),
        },
    )


def _request(
    floor: HardFloor,
    invoker: str = "alice",
    friction_confirmed: bool = True,
    now: datetime | None = None,
):
    return request_override(
        policies=_policies(),
        session_id=uuid4(),
        action_kind=CapabilityKind.SEND_EMAIL,
        target="alice@example.com",
        target_category_tier=("personal", "restricted"),
        floor=floor,
        invoker=invoker,
        friction_confirmed=friction_confirmed,
        now=now,
    )


# --- disallowed -----------------------------------------------------


def test_disallowed_refuses_authorized_invoker() -> None:
    """SC-014 — `disallowed` policy refuses every override, even when
    the invoker is otherwise authorized."""
    result = _request(HardFloor.PROHIBITED, invoker="alice")
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.POLICY_DISALLOWED


def test_missing_floor_entry_refuses_as_disallowed() -> None:
    """A floor without an operator entry refuses as DISALLOWED —
    absence is not permission (Principle VI)."""
    result = _request(HardFloor.INTEGRITY_FLOOR)
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.POLICY_DISALLOWED


# --- single-authorized ----------------------------------------------


def test_single_authorized_succeeds_for_authorized_invoker() -> None:
    result = _request(HardFloor.MAX_TIER_CLEARANCE, invoker="alice")
    assert isinstance(result, OverrideGrant)
    assert result.state == GrantState.ACTIVE
    assert result.invoker_principal == "alice"
    assert result.attester_principal is None
    assert result.expires_at > datetime.now(UTC)


def test_unauthorized_invoker_refused() -> None:
    result = _request(HardFloor.MAX_TIER_CLEARANCE, invoker="mallory")
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.UNAUTHORIZED_INVOKER


def test_friction_not_met_refused() -> None:
    result = _request(
        HardFloor.MAX_TIER_CLEARANCE,
        invoker="alice",
        friction_confirmed=False,
    )
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.FRICTION_NOT_MET


# --- dual-control ---------------------------------------------------


def test_dual_control_starts_pending_attestation() -> None:
    result = _request(HardFloor.ADMISSIBILITY_EXCLUSION)
    assert isinstance(result, OverrideGrant)
    assert result.state == GrantState.PENDING_ATTESTATION


def test_dual_control_invoker_cannot_self_attest() -> None:
    """SC-014 — distinct attester required. Invoker == attester is
    structurally refused."""
    grant = _request(HardFloor.ADMISSIBILITY_EXCLUSION)
    assert isinstance(grant, OverrideGrant)
    result = attest_override(grant, attester="alice", confirmed=True)
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.ATTESTER_SAME_AS_INVOKER


def test_dual_control_unauthorized_attester_refused() -> None:
    grant = _request(HardFloor.ADMISSIBILITY_EXCLUSION)
    assert isinstance(grant, OverrideGrant)
    result = attest_override(grant, attester="mallory", confirmed=True)
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.ATTESTER_UNAUTHORIZED


def test_dual_control_attestation_refusal_refused() -> None:
    grant = _request(HardFloor.ADMISSIBILITY_EXCLUSION)
    assert isinstance(grant, OverrideGrant)
    result = attest_override(grant, attester="bob", confirmed=False)
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.ATTESTATION_REFUSED


def test_dual_control_distinct_attester_succeeds() -> None:
    grant = _request(HardFloor.ADMISSIBILITY_EXCLUSION)
    assert isinstance(grant, OverrideGrant)
    result = attest_override(grant, attester="bob", confirmed=True)
    assert isinstance(result, OverrideGrant)
    assert result.state == GrantState.ACTIVE
    assert result.attester_principal == "bob"


# --- friction levels & expiry --------------------------------------


def test_prohibited_floor_default_friction_is_maximal() -> None:
    """The contract: prohibited and admissibility-exclusion are the
    gravest floors; default friction is MAXIMAL."""
    grant = _request(HardFloor.ADMISSIBILITY_EXCLUSION)
    assert isinstance(grant, OverrideGrant)
    assert grant.friction_level == FrictionLevel.MAXIMAL


def test_grant_has_non_null_expiry() -> None:
    """SC-011 — every grant carries a non-null expires_at; bounded
    grants only, no perpetual override."""
    now = datetime(2026, 5, 19, tzinfo=UTC)
    grant = _request(HardFloor.MAX_TIER_CLEARANCE, now=now)
    assert isinstance(grant, OverrideGrant)
    assert grant.expires_at == now + timedelta(seconds=60)
