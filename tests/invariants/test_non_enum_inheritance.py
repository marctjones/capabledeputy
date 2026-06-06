"""T119 invariant (FR-013): derived/delegated labels inherit
most-restrictive on every non-enumerated field.

For 003 Phase 2 the testable surfaces are:
  - AxisA per-category: tier = max, risk_ids = set-union,
    assignment_provenance = parent-wins (raise-only-inspector exception).
  - AxisB per-level: integrity_floor = OR.

Reversibility/bindings/capability callsites land alongside their
modules in later phases (T073/T074/T118 capability extension).
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.labels import (
    AxisA,
    AxisB,
    CategoryTag,
    ProvenanceLevel,
    ProvenanceTag,
    most_restrictive_inherit_axis_a,
    most_restrictive_inherit_axis_b,
)
from capabledeputy.policy.tiers import Tier


@pytest.mark.invariant
def test_axis_a_tier_is_most_restrictive() -> None:
    parent = AxisA(
        categories=(CategoryTag(category="health", tier=Tier.SENSITIVE),),
    )
    child = AxisA(
        categories=(CategoryTag(category="health", tier=Tier.REGULATED),),
    )
    merged = most_restrictive_inherit_axis_a(parent, child)
    assert len(merged.categories) == 1
    assert merged.categories[0].tier == Tier.REGULATED


@pytest.mark.invariant
def test_axis_a_risk_ids_set_union() -> None:
    parent = AxisA(
        categories=(
            CategoryTag(
                category="finance",
                tier=Tier.REGULATED,
                risk_ids=("R001", "R002"),
            ),
        ),
    )
    child = AxisA(
        categories=(
            CategoryTag(
                category="finance",
                tier=Tier.REGULATED,
                risk_ids=("R002", "R003"),
            ),
        ),
    )
    merged = most_restrictive_inherit_axis_a(parent, child)
    assert set(merged.categories[0].risk_ids) == {"R001", "R002", "R003"}


@pytest.mark.invariant
def test_axis_a_provenance_parent_wins_by_default() -> None:
    """Default rule: parent's assignment_provenance dominates a child's
    — derivation cannot wash provenance away."""
    parent = AxisA(
        categories=(
            CategoryTag(
                category="personal",
                tier=Tier.SENSITIVE,
                assignment_provenance="curated-mcp",
            ),
        ),
    )
    child = AxisA(
        categories=(
            CategoryTag(
                category="personal",
                tier=Tier.SENSITIVE,
                assignment_provenance="source-declared",
            ),
        ),
    )
    merged = most_restrictive_inherit_axis_a(parent, child)
    assert merged.categories[0].assignment_provenance == "curated-mcp"


@pytest.mark.invariant
def test_axis_a_provenance_raise_only_inspector_escalates() -> None:
    """Exception to parent-wins: raise-only-inspector escalates,
    because it represents added taint (inspector found something)."""
    parent = AxisA(
        categories=(
            CategoryTag(
                category="proprietary_work",
                tier=Tier.REGULATED,
                assignment_provenance="curated-mcp",
            ),
        ),
    )
    child = AxisA(
        categories=(
            CategoryTag(
                category="proprietary_work",
                tier=Tier.REGULATED,
                assignment_provenance="raise-only-inspector",
            ),
        ),
    )
    merged = most_restrictive_inherit_axis_a(parent, child)
    assert merged.categories[0].assignment_provenance == "raise-only-inspector"


@pytest.mark.invariant
def test_axis_a_new_category_in_child_added() -> None:
    parent = AxisA(
        categories=(CategoryTag(category="health", tier=Tier.REGULATED),),
    )
    child = AxisA(
        categories=(CategoryTag(category="finance", tier=Tier.REGULATED),),
    )
    merged = most_restrictive_inherit_axis_a(parent, child)
    cats = {c.category for c in merged.categories}
    assert cats == {"health", "finance"}


@pytest.mark.invariant
def test_axis_b_integrity_floor_is_or() -> None:
    parent = AxisB(
        entries=(ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED, integrity_floor=False),),
    )
    child = AxisB(
        entries=(ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED, integrity_floor=True),),
    )
    merged = most_restrictive_inherit_axis_b(parent, child)
    assert merged.entries[0].integrity_floor is True


@pytest.mark.invariant
def test_axis_b_levels_union() -> None:
    parent = AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    child = AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),))
    merged = most_restrictive_inherit_axis_b(parent, child)
    levels = {e.level for e in merged.entries}
    assert levels == {ProvenanceLevel.PRINCIPAL_DIRECT, ProvenanceLevel.EXTERNAL_UNTRUSTED}
