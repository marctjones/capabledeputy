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
    TrustProfile,
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


# --- trust profile (Slice A: operator-root default) -----------------


def _profile_request(
    policies: OverridePolicies,
    floor: HardFloor,
    invoker: str = "owner",
):
    return request_override(
        policies=policies,
        session_id=uuid4(),
        action_kind=CapabilityKind.SEND_EMAIL,
        target="x@example.com",
        target_category_tier=("personal", "restricted"),
        floor=floor,
        invoker=invoker,
        friction_confirmed=True,
    )


def test_managed_is_the_default_profile() -> None:
    """Absent config, the posture is `managed` — behavior is unchanged."""
    assert OverridePolicies(by_floor={}).trust_profile is TrustProfile.MANAGED


def test_managed_unlisted_floor_still_refuses() -> None:
    """Regression guard: in `managed`, an unlisted floor refuses as
    DISALLOWED exactly as before the trust-profile amendment."""
    policies = OverridePolicies(by_floor={}, trust_profile=TrustProfile.MANAGED)
    result = _profile_request(policies, HardFloor.MAX_TIER_CLEARANCE)
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.POLICY_DISALLOWED


def test_personal_unlisted_floor_defaults_to_solo_override() -> None:
    """`personal`: a floor the operator never configured defaults to
    single-authorized for the operator — they may solo-override it with
    friction, no second attester."""
    policies = OverridePolicies(
        by_floor={},
        trust_profile=TrustProfile.PERSONAL,
        operator_principal="owner",
    )
    for floor in HardFloor:  # every floor, including prohibited
        grant = _profile_request(policies, floor, invoker="owner")
        assert isinstance(grant, OverrideGrant), floor
        assert grant.state == GrantState.ACTIVE, floor
        assert grant.invoker_principal == "owner"
        assert grant.attester_principal is None  # solo, no attester


def test_personal_default_still_rejects_non_operator() -> None:
    """The personal default authorizes ONLY the operator principal — a
    different principal is still UNAUTHORIZED (the model is never a
    principal here, so it can never reach this path)."""
    policies = OverridePolicies(
        by_floor={},
        trust_profile=TrustProfile.PERSONAL,
        operator_principal="owner",
    )
    result = _profile_request(policies, HardFloor.PROHIBITED, invoker="mallory")
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.UNAUTHORIZED_INVOKER


def test_personal_explicit_entry_overrides_the_default() -> None:
    """Explicit `by_floor` entries always win — a personal operator can
    pin a grave floor back to `disallowed` (the default does not weaken an
    operator's deliberate lock-down)."""
    policies = OverridePolicies(
        by_floor={
            HardFloor.PROHIBITED: OverridePolicyEntry(
                floor=HardFloor.PROHIBITED,
                policy=OverridePolicy.DISALLOWED,
            ),
        },
        trust_profile=TrustProfile.PERSONAL,
        operator_principal="owner",
    )
    refused = _profile_request(policies, HardFloor.PROHIBITED, invoker="owner")
    assert isinstance(refused, OverrideRefusal)
    assert refused.reason == OverrideRefusalReason.POLICY_DISALLOWED
    # ...while an unconfigured floor still gets the solo default.
    granted = _profile_request(policies, HardFloor.INTEGRITY_FLOOR, invoker="owner")
    assert isinstance(granted, OverrideGrant)


def test_personal_default_friction_scales_by_floor() -> None:
    """The synthesized personal default still scales friction by floor —
    prohibited stays MAXIMAL even though it became solo-overridable."""
    policies = OverridePolicies(
        by_floor={},
        trust_profile=TrustProfile.PERSONAL,
        operator_principal="owner",
    )
    grant = _profile_request(policies, HardFloor.PROHIBITED, invoker="owner")
    assert isinstance(grant, OverrideGrant)
    assert grant.friction_level == FrictionLevel.MAXIMAL


# --- trust profile: loader (fail-closed) ----------------------------


def test_load_personal_requires_operator_principal(tmp_path) -> None:
    """Fail-closed: `personal` without an operator_principal is refused at
    load — it would otherwise silently refuse every default override."""
    import pytest

    from capabledeputy.policy.overrides import OverrideError, load

    p = tmp_path / "override_policy.yaml"
    p.write_text("trust_profile: personal\npolicies: []\n", encoding="utf-8")
    with pytest.raises(OverrideError, match="operator_principal"):
        load(p)


def test_load_personal_with_operator_parses(tmp_path) -> None:
    from capabledeputy.policy.overrides import load

    p = tmp_path / "override_policy.yaml"
    p.write_text(
        "trust_profile: personal\noperator_principal: owner\npolicies: []\n",
        encoding="utf-8",
    )
    policies = load(p)
    assert policies.trust_profile is TrustProfile.PERSONAL
    assert policies.operator_principal == "owner"
    # the parsed personal config yields the solo default end to end
    grant = _profile_request(policies, HardFloor.MAX_TIER_CLEARANCE, invoker="owner")
    assert isinstance(grant, OverrideGrant)


def test_load_default_is_managed(tmp_path) -> None:
    from capabledeputy.policy.overrides import load

    p = tmp_path / "override_policy.yaml"
    p.write_text("policies: []\n", encoding="utf-8")
    assert load(p).trust_profile is TrustProfile.MANAGED
