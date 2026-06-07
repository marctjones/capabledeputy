"""T119 invariant (FR-013): derived/delegated labels inherit
most-restrictive on every non-enumerated field.

For 003 Phase 2 the testable surfaces are (now on LabelState):
  - Axis A per-category: tier = max, risk_ids = set-union,
    assignment_provenance = parent-wins (raise-only-inspector exception).
  - Axis B per-level: provenance union.

Reversibility/bindings/capability callsites land alongside their
modules in later phases (T073/T074/T118 capability extension).
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
    inherit,
    most_restrictive_inherit,
)
from capabledeputy.policy.tiers import Tier


@pytest.mark.invariant
def test_axis_a_tier_is_most_restrictive() -> None:
    parent = LabelState(
        a=frozenset({CategoryTag(category="health", tier=Tier.SENSITIVE)}),
    )
    child = LabelState(
        a=frozenset({CategoryTag(category="health", tier=Tier.REGULATED)}),
    )
    merged = most_restrictive_inherit(parent, child)
    assert len(merged.a) == 1
    assert next(iter(merged.a)).tier == Tier.REGULATED


@pytest.mark.invariant
def test_axis_a_risk_ids_set_union() -> None:
    parent = LabelState(
        a=frozenset({
            CategoryTag(
                category="finance",
                tier=Tier.REGULATED,
                risk_ids=("R001", "R002"),
            ),
        }),
    )
    child = LabelState(
        a=frozenset({
            CategoryTag(
                category="finance",
                tier=Tier.REGULATED,
                risk_ids=("R002", "R003"),
            ),
        }),
    )
    merged = most_restrictive_inherit(parent, child)
    assert set(next(iter(merged.a)).risk_ids) == {"R001", "R002", "R003"}


@pytest.mark.invariant
def test_axis_a_provenance_parent_wins_by_default() -> None:
    """Default rule: parent's assignment_provenance dominates a child's
    — derivation cannot wash provenance away."""
    parent = LabelState(
        a=frozenset({
            CategoryTag(
                category="personal",
                tier=Tier.SENSITIVE,
                assignment_provenance="curated-mcp",
            ),
        }),
    )
    child = LabelState(
        a=frozenset({
            CategoryTag(
                category="personal",
                tier=Tier.SENSITIVE,
                assignment_provenance="source-declared",
            ),
        }),
    )
    merged = inherit(parent, child)
    assert next(iter(merged.a)).assignment_provenance == "curated-mcp"


@pytest.mark.invariant
def test_axis_a_provenance_raise_only_inspector_escalates() -> None:
    """Exception to parent-wins: raise-only-inspector escalates,
    because it represents added taint (inspector found something)."""
    parent = LabelState(
        a=frozenset({
            CategoryTag(
                category="proprietary_work",
                tier=Tier.REGULATED,
                assignment_provenance="curated-mcp",
            ),
        }),
    )
    child = LabelState(
        a=frozenset({
            CategoryTag(
                category="proprietary_work",
                tier=Tier.REGULATED,
                assignment_provenance="raise-only-inspector",
            ),
        }),
    )
    merged = inherit(parent, child)
    assert next(iter(merged.a)).assignment_provenance == "raise-only-inspector"


@pytest.mark.invariant
def test_axis_a_new_category_in_child_added() -> None:
    parent = LabelState(
        a=frozenset({CategoryTag(category="health", tier=Tier.REGULATED)}),
    )
    child = LabelState(
        a=frozenset({CategoryTag(category="finance", tier=Tier.REGULATED)}),
    )
    merged = most_restrictive_inherit(parent, child)
    cats = {c.category for c in merged.a}
    assert cats == {"health", "finance"}


@pytest.mark.invariant
def test_axis_b_levels_union() -> None:
    parent = LabelState(
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    child = LabelState(
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
    )
    merged = most_restrictive_inherit(parent, child)
    levels = {e.level for e in merged.b}
    assert levels == {ProvenanceLevel.PRINCIPAL_DIRECT, ProvenanceLevel.EXTERNAL_UNTRUSTED}
