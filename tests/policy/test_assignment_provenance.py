"""T033 (003 US1, FR-022): assignment_provenance is preserved across
resolution chains; raise-only-inspector adds taint but never clears.

The semantic verified here is composition behavior (T118
most_restrictive_inherit_axis_a): when two AxisA sets merge, the
parent's provenance dominates by default; raise-only-inspector is
the documented exception (it represents added taint).
"""

from __future__ import annotations

from capabledeputy.policy.labels import (
    AxisA,
    AxisACategory,
    most_restrictive_inherit_axis_a,
)
from capabledeputy.policy.tiers import Tier


def test_assignment_provenance_parent_dominates() -> None:
    parent = AxisA(
        categories=(
            AxisACategory(
                category="health",
                tier=Tier.REGULATED,
                assignment_provenance="curated-mcp",
            ),
        ),
    )
    child = AxisA(
        categories=(
            AxisACategory(
                category="health",
                tier=Tier.REGULATED,
                assignment_provenance="source-declared",
            ),
        ),
    )
    merged = most_restrictive_inherit_axis_a(parent, child)
    assert merged.categories[0].assignment_provenance == "curated-mcp"


def test_raise_only_inspector_does_not_clear_taint() -> None:
    """The raise-only-inspector value is the only provenance that
    can replace a parent's provenance — and it only ever *raises*
    (adds taint), never clears it. Verified here by composition:
    the merged tier is at least as high as the higher of the two
    inputs, and the provenance flips to raise-only-inspector."""
    parent = AxisA(
        categories=(
            AxisACategory(
                category="proprietary_work",
                tier=Tier.REGULATED,
                assignment_provenance="curated-mcp",
            ),
        ),
    )
    inspector = AxisA(
        categories=(
            AxisACategory(
                category="proprietary_work",
                tier=Tier.RESTRICTED,  # inspector raised the tier
                assignment_provenance="raise-only-inspector",
            ),
        ),
    )
    merged = most_restrictive_inherit_axis_a(parent, inspector)
    assert merged.categories[0].tier == Tier.RESTRICTED
    assert merged.categories[0].assignment_provenance == "raise-only-inspector"


def test_provenance_preserved_when_new_category_added() -> None:
    parent = AxisA(
        categories=(
            AxisACategory(
                category="health",
                tier=Tier.REGULATED,
                assignment_provenance="source-declared",
            ),
        ),
    )
    child = AxisA(
        categories=(
            AxisACategory(
                category="finance",
                tier=Tier.REGULATED,
                assignment_provenance="curated-mcp",
            ),
        ),
    )
    merged = most_restrictive_inherit_axis_a(parent, child)
    by_cat = {c.category: c for c in merged.categories}
    assert by_cat["health"].assignment_provenance == "source-declared"
    assert by_cat["finance"].assignment_provenance == "curated-mcp"
