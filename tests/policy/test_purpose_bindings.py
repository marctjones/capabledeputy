"""Tests for per-purpose source/location bindings.

Operators declare per-purpose bindings in configs/purposes.yaml;
LabeledToolClient composes them with the global BindingSet at
chokepoint time. Same path can resolve to different labels in
different purposes (research vs. tax-prep).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.bindings import BindingSet, SourceLocationLabelBinding
from capabledeputy.policy.purposes import (
    Purpose,
    PurposeError,
    Purposes,
    load,
)
from capabledeputy.policy.tiers import Tier

# ---------- parser ----------


def test_parse_purpose_bindings_basic(tmp_path: Path) -> None:
    cfg = tmp_path / "purposes.yaml"
    cfg.write_text(
        """
purposes:
  - purpose_id: research
    admissible_categories: [research]
    bindings:
      - scope_pattern_canonical: "file:///home/me/research/**"
        category: research
        default_tier: none
      - scope_pattern_canonical: "file:///home/me/research/private/**"
        category: research
        default_tier: sensitive
""",
        encoding="utf-8",
    )
    purposes = load(cfg)
    p = purposes.get("research")
    assert p is not None
    assert len(p.bindings) == 2
    patterns = {b.scope_pattern_canonical for b in p.bindings}
    assert "file:///home/me/research/**" in patterns
    assert "file:///home/me/research/private/**" in patterns


def test_parse_purpose_bindings_missing_field_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "purposes.yaml"
    cfg.write_text(
        """
purposes:
  - purpose_id: bad
    admissible_categories: [public]
    bindings:
      - scope_pattern_canonical: "file:///foo/**"
        category: foo
        # missing default_tier
""",
        encoding="utf-8",
    )
    with pytest.raises(PurposeError, match="missing"):
        load(cfg)


def test_parse_purpose_bindings_unknown_tier_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "purposes.yaml"
    cfg.write_text(
        """
purposes:
  - purpose_id: bad
    admissible_categories: [public]
    bindings:
      - scope_pattern_canonical: "file:///foo/**"
        category: foo
        default_tier: NOT_A_REAL_TIER
""",
        encoding="utf-8",
    )
    with pytest.raises(PurposeError, match="unknown default_tier"):
        load(cfg)


def test_parse_no_bindings_yields_empty_tuple(tmp_path: Path) -> None:
    cfg = tmp_path / "purposes.yaml"
    cfg.write_text(
        """
purposes:
  - purpose_id: minimal
    admissible_categories: [public]
""",
        encoding="utf-8",
    )
    purposes = load(cfg)
    minimal = purposes.get("minimal")
    assert minimal is not None
    assert minimal.bindings == ()


# ---------- composition semantics ----------


def test_purpose_bindings_override_global_via_specificity() -> None:
    """A purpose's narrow path-binding wins over a broad global one.

    No explicit precedence logic needed — the existing
    BindingSet.resolve()'s most-specific-wins rule handles it.
    """
    global_set = BindingSet(
        bindings=(
            SourceLocationLabelBinding(
                name="test",
                scope_pattern_canonical="file:///home/me/**",
                category="general",
                default_tier=Tier.SENSITIVE,
                assignment_provenance="global",
            ),
        ),
    )
    purpose_bindings = (
        SourceLocationLabelBinding(
            name="test",
            scope_pattern_canonical="file:///home/me/research/**",
            category="research",
            default_tier=Tier.NONE,
            assignment_provenance="purpose-declared",
        ),
    )
    composed = BindingSet(
        bindings=(*global_set.bindings, *purpose_bindings),
    )

    # A path inside research/ — purpose wins
    r1 = composed.resolve("file:///home/me/research/notes.md")
    assert r1.category == "research"
    assert r1.tier == Tier.NONE

    # A path outside research/ — global still applies
    r2 = composed.resolve("file:///home/me/secrets/keys.txt")
    assert r2.category == "general"
    assert r2.tier == Tier.SENSITIVE


def test_same_path_different_purposes_different_labels() -> None:
    """The same path resolved through two different purposes' composed
    BindingSets produces different category/tier results — the core
    win of per-purpose bindings."""
    base_global = BindingSet(
        bindings=(
            SourceLocationLabelBinding(
                name="test",
                scope_pattern_canonical="file:///shared/**",
                category="shared",
                default_tier=Tier.NONE,
                assignment_provenance="global",
            ),
        ),
    )

    research_purpose = Purpose(
        purpose_id="research",
        admissible_categories=frozenset({"research"}),
        bindings=(
            SourceLocationLabelBinding(
                name="test",
                scope_pattern_canonical="file:///shared/datasets/**",
                category="research",
                default_tier=Tier.NONE,
                assignment_provenance="purpose-declared",
            ),
        ),
    )
    tax_purpose = Purpose(
        purpose_id="tax-prep",
        admissible_categories=frozenset({"finance"}),
        bindings=(
            SourceLocationLabelBinding(
                name="test",
                scope_pattern_canonical="file:///shared/datasets/**",
                category="finance",
                default_tier=Tier.RESTRICTED,
                assignment_provenance="purpose-declared",
            ),
        ),
    )

    research_composed = BindingSet(
        bindings=(*base_global.bindings, *research_purpose.bindings),
    )
    tax_composed = BindingSet(
        bindings=(*base_global.bindings, *tax_purpose.bindings),
    )

    path = "file:///shared/datasets/2026/q1.csv"
    r_research = research_composed.resolve(path)
    r_tax = tax_composed.resolve(path)

    # Same path, different labels because the active purpose
    # contributed a different binding.
    assert r_research.category == "research"
    assert r_research.tier == Tier.NONE
    assert r_tax.category == "finance"
    assert r_tax.tier == Tier.RESTRICTED


def test_purpose_with_no_bindings_falls_through_to_global() -> None:
    global_set = BindingSet(
        bindings=(
            SourceLocationLabelBinding(
                name="test",
                scope_pattern_canonical="file:///**",
                category="anything",
                default_tier=Tier.NONE,
                assignment_provenance="global",
            ),
        ),
    )
    purpose = Purpose(
        purpose_id="basic",
        admissible_categories=frozenset({"anything"}),
        # No bindings
    )
    # When the purpose has no bindings, composition is just the global set.
    composed = BindingSet(
        bindings=(*global_set.bindings, *purpose.bindings),
    )
    r = composed.resolve("file:///somewhere/file.txt")
    assert r.category == "anything"


def test_purposes_registry_lookup() -> None:
    """The Purposes registry holds purposes by id; lookup is the
    composition entry point used by LabeledToolClient."""
    p_research = Purpose(
        purpose_id="research",
        admissible_categories=frozenset({"research"}),
        bindings=(
            SourceLocationLabelBinding(
                name="test",
                scope_pattern_canonical="file:///research/**",
                category="research",
                default_tier=Tier.NONE,
            ),
        ),
    )
    registry = Purposes(purposes={"research": p_research})
    found = registry.get("research")
    assert found is not None
    assert len(found.bindings) == 1
    assert registry.get("unknown") is None
