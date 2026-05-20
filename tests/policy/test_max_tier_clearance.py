"""T096 — Max-tier clearance read-up refusal (FR-008 / US5 scenario 1).

A ContextProfile carries a `max_tier`; reads of data above that
tier are refused. The check is on the *resolved* tier (post-
resolve_tier()), so profiles with category overrides that raise the
tier still get gated by the clearance check.

Marquee scenario: a profile cleared to `regulated` cannot read a
`restricted` datum.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.resolution import (
    ClearanceRefusedError,
    ContextProfile,
    check_max_tier_clearance,
)
from capabledeputy.policy.tiers import Tier


def test_open_clearance_passes_any_tier() -> None:
    """A profile with no max_tier has open clearance — every tier
    passes the check. Operator opts in to clearance by setting
    max_tier on the profile."""
    profile = ContextProfile(id="open", max_tier=None)
    for tier in (Tier.NONE, Tier.SENSITIVE, Tier.REGULATED, Tier.RESTRICTED, Tier.PROHIBITED):
        check_max_tier_clearance(profile=profile, attempted_tier=tier)


def test_clearance_passes_at_exact_max_tier() -> None:
    """`max_tier = regulated` permits a regulated read — the check
    is strict-greater, not strict-greater-or-equal."""
    profile = ContextProfile(id="reg", max_tier=Tier.REGULATED)
    check_max_tier_clearance(profile=profile, attempted_tier=Tier.REGULATED)


def test_clearance_passes_below_max_tier() -> None:
    profile = ContextProfile(id="reg", max_tier=Tier.REGULATED)
    check_max_tier_clearance(profile=profile, attempted_tier=Tier.SENSITIVE)
    check_max_tier_clearance(profile=profile, attempted_tier=Tier.NONE)


def test_regulated_profile_refuses_restricted_datum() -> None:
    """SC-008 scenario 1 — regulated cleared, restricted datum ⇒
    refused (FR-008)."""
    profile = ContextProfile(id="reg-only", max_tier=Tier.REGULATED)
    with pytest.raises(ClearanceRefusedError) as exc:
        check_max_tier_clearance(profile=profile, attempted_tier=Tier.RESTRICTED)
    assert exc.value.profile_id == "reg-only"
    assert exc.value.profile_max_tier == Tier.REGULATED
    assert exc.value.attempted_tier == Tier.RESTRICTED


def test_regulated_profile_refuses_prohibited_datum() -> None:
    """Belt-and-suspenders: prohibited is two tiers above regulated;
    still refused."""
    profile = ContextProfile(id="reg-only", max_tier=Tier.REGULATED)
    with pytest.raises(ClearanceRefusedError):
        check_max_tier_clearance(profile=profile, attempted_tier=Tier.PROHIBITED)


def test_sensitive_profile_refuses_regulated() -> None:
    profile = ContextProfile(id="sensitive-only", max_tier=Tier.SENSITIVE)
    with pytest.raises(ClearanceRefusedError):
        check_max_tier_clearance(profile=profile, attempted_tier=Tier.REGULATED)


def test_none_clearance_refuses_anything_above() -> None:
    """A clearance of `none` (Tier.NONE) admits NO labeled tier —
    a read of any sensitive-or-above datum refuses."""
    profile = ContextProfile(id="zero", max_tier=Tier.NONE)
    with pytest.raises(ClearanceRefusedError):
        check_max_tier_clearance(profile=profile, attempted_tier=Tier.SENSITIVE)
