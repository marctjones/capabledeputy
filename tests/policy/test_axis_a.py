"""T021 invariants for 003 US1 (FR-003).

Axis A behavior:
- Data categories stay distinct (FR-003 lattice compartments).
- Each persisted label carries assignment_provenance.
- fixed-high resolution mode cannot be lowered by any profile
  (also covered in test_resolution.py; here we verify the
  assignment_provenance is preserved through the resolution path).
"""

from __future__ import annotations

from capabledeputy.policy.labels import AxisA, CategoryTag
from capabledeputy.policy.tiers import Tier


def test_categories_are_distinct_in_axis_a() -> None:
    """FR-003: distinct categories occupy distinct slots; one does
    not subsume another (lattice compartments, not a hierarchy)."""
    axis = AxisA(
        categories=(
            CategoryTag(category="health", tier=Tier.REGULATED),
            CategoryTag(category="financial", tier=Tier.REGULATED),
            CategoryTag(category="personal", tier=Tier.SENSITIVE),
        ),
    )
    ids = {c.category for c in axis.categories}
    assert ids == {"health", "financial", "personal"}


def test_axis_a_category_carries_assignment_provenance() -> None:
    """Every CategoryTag carries assignment_provenance (FR-022 trace).
    Default for direct construction is 'system-default'."""
    cat = CategoryTag(category="health", tier=Tier.REGULATED)
    assert cat.assignment_provenance == "system-default"

    explicit = CategoryTag(
        category="proprietary_work",
        tier=Tier.RESTRICTED,
        assignment_provenance="source-declared",
    )
    assert explicit.assignment_provenance == "source-declared"


def test_axis_a_round_trip_preserves_provenance() -> None:
    """to_dict/from_dict must round-trip assignment_provenance —
    losing it would silently break replayability (SC-002)."""
    cat = CategoryTag(
        category="health",
        tier=Tier.RESTRICTED,
        risk_ids=("R001", "R002"),
        assignment_provenance="curated-mcp",
    )
    axis = AxisA(categories=(cat,))
    restored = AxisA.from_dict(axis.to_dict())
    assert restored.categories[0].assignment_provenance == "curated-mcp"
    assert restored.categories[0].risk_ids == ("R001", "R002")


def test_axis_a_empty_round_trip() -> None:
    """An empty AxisA round-trips cleanly (default for new sessions)."""
    axis = AxisA()
    assert AxisA.from_dict(axis.to_dict()).categories == ()
    # None input also yields empty (default-tolerant per
    # Constitution §Sec. Constraints).
    assert AxisA.from_dict(None).categories == ()
