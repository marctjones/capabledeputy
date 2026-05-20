"""T022 / T023 invariants for 003 US1 (FR-007, SC-002).

Deterministic sensitivity-resolution layer:
- Same inputs ⇒ byte-identical (tier, rationale).
- Conflicting profiles ⇒ most-restrictive composition baseline.
- fixed-high mode immovable (T021 scenario 3 — also tested here).
- Unknown category/profile ⇒ ResolutionError (Principle VI fail-closed).
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.resolution import (
    Category,
    CategoryOverride,
    ContextProfile,
    ResolutionError,
    resolve_tier,
)
from capabledeputy.policy.tiers import Tier


def _two_profiles_for_health() -> tuple[dict[str, Category], dict[str, ContextProfile]]:
    """Standard US1 fixture: a `health` category in context-up mode,
    a `clinician` profile that raises it to REGULATED, a `general`
    profile that leaves the default in place."""
    categories = {
        "health": Category(
            id="health",
            default_tier=Tier.SENSITIVE,
            resolution_mode="context-up",
        ),
    }
    profiles = {
        "clinician": ContextProfile(
            id="clinician",
            use_case="clinical",
            category_overrides=(CategoryOverride(category="health", tier=Tier.REGULATED),),
        ),
        "general": ContextProfile(id="general", use_case="general"),
    }
    return categories, profiles


def test_resolve_default_tier_when_no_profile_overrides() -> None:
    cats, profs = _two_profiles_for_health()
    result = resolve_tier(
        category_id="health",
        profile_ids=("general",),
        categories=cats,
        profiles=profs,
    )
    assert result.tier == Tier.SENSITIVE
    assert "category=health" in result.rationale


def test_resolve_clinician_profile_raises_to_regulated() -> None:
    cats, profs = _two_profiles_for_health()
    result = resolve_tier(
        category_id="health",
        profile_ids=("clinician",),
        categories=cats,
        profiles=profs,
    )
    assert result.tier == Tier.REGULATED
    assert result.contributing_profile_ids == ("clinician",)


def test_resolve_determinism_same_inputs_same_output() -> None:
    """SC-002: same inputs ⇒ byte-identical (tier, rationale)."""
    cats, profs = _two_profiles_for_health()
    r1 = resolve_tier(
        category_id="health",
        profile_ids=("clinician", "general"),
        categories=cats,
        profiles=profs,
    )
    r2 = resolve_tier(
        category_id="health",
        profile_ids=("clinician", "general"),
        categories=cats,
        profiles=profs,
    )
    assert r1.tier == r2.tier
    assert r1.rationale == r2.rationale
    assert r1.contributing_profile_ids == r2.contributing_profile_ids


def test_resolve_determinism_permutation_invariant() -> None:
    """SC-002: permuting profile_ids must not change output."""
    cats, profs = _two_profiles_for_health()
    r1 = resolve_tier(
        category_id="health",
        profile_ids=("clinician", "general"),
        categories=cats,
        profiles=profs,
    )
    r2 = resolve_tier(
        category_id="health",
        profile_ids=("general", "clinician"),
        categories=cats,
        profiles=profs,
    )
    assert r1.tier == r2.tier
    assert r1.rationale == r2.rationale


def test_resolve_conflicting_profiles_most_restrictive() -> None:
    """When two profiles both override, most-restrictive wins (FR-026a)."""
    categories = {
        "personal": Category(
            id="personal",
            default_tier=Tier.SENSITIVE,
            resolution_mode="context-up",
        ),
    }
    profiles = {
        "low_friction": ContextProfile(
            id="low_friction",
            category_overrides=(CategoryOverride(category="personal", tier=Tier.SENSITIVE),),
        ),
        "high_friction": ContextProfile(
            id="high_friction",
            category_overrides=(CategoryOverride(category="personal", tier=Tier.RESTRICTED),),
        ),
    }
    result = resolve_tier(
        category_id="personal",
        profile_ids=("low_friction", "high_friction"),
        categories=categories,
        profiles=profiles,
    )
    assert result.tier == Tier.RESTRICTED


def test_fixed_high_mode_cannot_be_lowered_by_profile() -> None:
    """T021 scenario 3 / FR-007: fixed-high categories are immovable
    regardless of profile overrides."""
    categories = {
        "financial": Category(
            id="financial",
            default_tier=Tier.RESTRICTED,
            resolution_mode="fixed-high",
        ),
    }
    profiles = {
        "lenient": ContextProfile(
            id="lenient",
            category_overrides=(CategoryOverride(category="financial", tier=Tier.SENSITIVE),),
        ),
    }
    result = resolve_tier(
        category_id="financial",
        profile_ids=("lenient",),
        categories=categories,
        profiles=profiles,
    )
    # The lenient override is IGNORED because the category mode is fixed-high.
    assert result.tier == Tier.RESTRICTED
    # The profile was consulted (recorded), even though its override was ignored.
    assert result.contributing_profile_ids == ("lenient",)


def test_unknown_category_fails_closed() -> None:
    """Principle VI: unknown category ⇒ ResolutionError, not best-effort allow."""
    with pytest.raises(ResolutionError, match="unknown category"):
        resolve_tier(
            category_id="nonexistent",
            profile_ids=(),
            categories={},
            profiles={},
        )


def test_unknown_profile_fails_closed() -> None:
    cats, _ = _two_profiles_for_health()
    with pytest.raises(ResolutionError, match="unknown profile"):
        resolve_tier(
            category_id="health",
            profile_ids=("ghost",),
            categories=cats,
            profiles={},
        )


def test_context_up_mode_ignores_lowering_overrides() -> None:
    categories = {
        "research": Category(
            id="research",
            default_tier=Tier.REGULATED,
            resolution_mode="context-up",
        ),
    }
    profiles = {
        "raiser": ContextProfile(
            id="raiser",
            category_overrides=(CategoryOverride(category="research", tier=Tier.RESTRICTED),),
        ),
        "lowerer": ContextProfile(
            id="lowerer",
            category_overrides=(CategoryOverride(category="research", tier=Tier.SENSITIVE),),
        ),
    }
    # lowerer attempts to drop to SENSITIVE; context-up ignores it.
    # raiser raises to RESTRICTED. Result: RESTRICTED.
    result = resolve_tier(
        category_id="research",
        profile_ids=("raiser", "lowerer"),
        categories=categories,
        profiles=profiles,
    )
    assert result.tier == Tier.RESTRICTED
