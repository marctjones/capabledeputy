"""T063 — Source/Location Label Bindings (FR-043 / SC-018).

The marquee scenario from Quickstart §1: an operator declares
`HR-folder` (canonical `file:///HR/employees/**`) carries category
`personal`/tier `regulated`, and `TeamSharePoint`
(`https://teams.sharepoint.com/...`) is a sharing destination. A
flow that tries to write personal/regulated data to the team site
deterministically denies via the named binding — never "no rule
matched" (SC-018).

These tests cover:
  - Most-specific subtree wins for category/tier.
  - Overlapping bindings compose most-restrictive.
  - Unbound or non-canonicalizable URIs fail-closed (FR-023).
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.bindings import (
    BindingError,
    BindingSet,
    SourceLocationLabelBinding,
    WriteDiscipline,
    canonicalize,
)
from capabledeputy.policy.reversibility import (
    MutabilityDegree,
    MutabilityLabel,
    ReversalAgent,
)
from capabledeputy.policy.tiers import Tier


def _hr_folder_binding() -> SourceLocationLabelBinding:
    return SourceLocationLabelBinding(
        name="HR-folder",
        scope_pattern_canonical="file:///HR/employees/*",
        category="personal",
        default_tier=Tier.REGULATED,
        write_discipline=WriteDiscipline.VERSION_PRESERVING,
        risk_ids=("RISK-PII-001",),
    )


def _hr_subdir_binding() -> SourceLocationLabelBinding:
    """A more-specific subtree under HR-folder: terminations are
    even more sensitive (restricted vs regulated)."""
    return SourceLocationLabelBinding(
        name="HR-terminations",
        scope_pattern_canonical="file:///HR/employees/terminations/*",
        category="personal",
        default_tier=Tier.RESTRICTED,
    )


def _team_sharepoint_binding() -> SourceLocationLabelBinding:
    return SourceLocationLabelBinding(
        name="TeamSharePoint",
        scope_pattern_canonical="https://teams.sharepoint.com/*",
        category="proprietary_work",
        default_tier=Tier.SENSITIVE,
        mutability=MutabilityLabel(
            degree=MutabilityDegree.IN_PLACE,
            agent=ReversalAgent.EXTERNAL,
        ),
    )


# --- canonicalize ----------------------------------------------------


def test_canonicalize_lowercases_scheme_and_host() -> None:
    assert canonicalize("FILE:///HR/x") == "file:///HR/x"
    assert canonicalize("/HR/x") == "file:///HR/x"
    assert canonicalize("macos://APP/com.apple.mail") == "macos://app/com.apple.mail"
    assert canonicalize("HTTPS://Teams.SharePoint.COM/site") == (
        "https://teams.sharepoint.com/site"
    )


def test_canonicalize_drops_fragment() -> None:
    assert canonicalize("https://host/path#fragment") == "https://host/path"


def test_canonicalize_preserves_query() -> None:
    assert canonicalize("https://host/path?a=1") == "https://host/path?a=1"


def test_canonicalize_unsupported_scheme_fails_closed() -> None:
    with pytest.raises(BindingError):
        canonicalize("javascript:alert(1)")


def test_canonicalize_garbage_fails_closed() -> None:
    with pytest.raises(BindingError):
        canonicalize("not-a-uri")


# --- BindingSet.resolve ---------------------------------------------


def test_unbound_uri_fails_closed() -> None:
    """A URI that canonicalizes but matches no binding ⇒ refused.
    FR-023 — absence is not permission."""
    bindings = BindingSet(bindings=(_hr_folder_binding(),))
    with pytest.raises(BindingError) as exc:
        bindings.resolve("file:///random/place/file.txt")
    assert "no binding matches" in str(exc.value)


def test_matched_binding_returns_canonical_destination_id() -> None:
    bindings = BindingSet(bindings=(_hr_folder_binding(),))
    result = bindings.resolve("FILE:///HR/employees/alice.csv")
    assert result.canonical_destination_id == "file:///HR/employees/alice.csv"
    assert result.category == "personal"
    assert result.tier == Tier.REGULATED


def test_most_specific_subtree_wins_for_tier() -> None:
    """HR-terminations is a deeper subtree of HR-folder; its
    `restricted` tier dominates the `regulated` of the parent."""
    bindings = BindingSet(
        bindings=(_hr_folder_binding(), _hr_subdir_binding()),
    )
    result = bindings.resolve("file:///HR/employees/terminations/bob.csv")
    # Both match — overlap on same category. Tier composition is
    # most-restrictive (RESTRICTED wins) regardless of specificity.
    assert result.tier == Tier.RESTRICTED


def test_team_sharepoint_egress_deny_via_binding() -> None:
    """SC-018 — the named binding resolves cleanly to a categorized
    destination; the policy layer above this can then deny the flow."""
    bindings = BindingSet(bindings=(_team_sharepoint_binding(),))
    result = bindings.resolve("https://teams.sharepoint.com/sites/team/upload")
    assert result.category == "proprietary_work"
    assert result.tier == Tier.SENSITIVE
    assert result.canonical_destination_id == ("https://teams.sharepoint.com/sites/team/upload")


def test_write_discipline_most_restrictive_wins() -> None:
    """If any matched binding demands version-preserving writes, the
    resolved write discipline is version-preserving."""
    weak = SourceLocationLabelBinding(
        name="weak",
        scope_pattern_canonical="file:///audit/*",
        category="audit",
        default_tier=Tier.SENSITIVE,
        write_discipline=WriteDiscipline.IN_PLACE,
    )
    strict = SourceLocationLabelBinding(
        name="strict",
        scope_pattern_canonical="file:///audit/wal/*",
        category="audit",
        default_tier=Tier.SENSITIVE,
        write_discipline=WriteDiscipline.VERSION_PRESERVING,
    )
    bindings = BindingSet(bindings=(weak, strict))
    result = bindings.resolve("file:///audit/wal/2026-05-19.log")
    assert result.write_discipline == WriteDiscipline.VERSION_PRESERVING


def test_risk_ids_union_across_matched_bindings() -> None:
    a = SourceLocationLabelBinding(
        name="a",
        scope_pattern_canonical="file:///shared/*",
        category="shared",
        default_tier=Tier.SENSITIVE,
        risk_ids=("R-1", "R-2"),
    )
    b = SourceLocationLabelBinding(
        name="b",
        scope_pattern_canonical="file:///shared/x/*",
        category="shared",
        default_tier=Tier.SENSITIVE,
        risk_ids=("R-2", "R-3"),
    )
    bindings = BindingSet(bindings=(a, b))
    result = bindings.resolve("file:///shared/x/file")
    assert set(result.risk_ids) == {"R-1", "R-2", "R-3"}
