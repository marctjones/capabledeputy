"""T021 invariants for 003 US1 (FR-003).

Axis A behavior (via LabelState):
- Data categories stay distinct (FR-003 lattice compartments).
- Each persisted label carries assignment_provenance.
- fixed-high resolution mode cannot be lowered by any profile
  (also covered in test_resolution.py; here we verify the
  assignment_provenance is preserved through the resolution path).
"""

from __future__ import annotations

from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier


def test_categories_are_distinct_in_label_state() -> None:
    """FR-003: distinct categories occupy distinct slots; one does
    not subsume another (lattice compartments, not a hierarchy)."""
    state = LabelState(
        a=frozenset({
            CategoryTag(category="health", tier=Tier.REGULATED),
            CategoryTag(category="financial", tier=Tier.REGULATED),
            CategoryTag(category="personal", tier=Tier.SENSITIVE),
        }),
    )
    ids = {c.category for c in state.a}
    assert ids == {"health", "financial", "personal"}


def test_category_tag_carries_assignment_provenance() -> None:
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


def test_label_state_round_trip_preserves_provenance() -> None:
    """to_dict/from_dict must round-trip assignment_provenance —
    losing it would silently break replayability (SC-002)."""
    cat = CategoryTag(
        category="health",
        tier=Tier.RESTRICTED,
        risk_ids=("R001", "R002"),
        assignment_provenance="curated-mcp",
    )
    state = LabelState(a=frozenset({cat}))
    restored = LabelState.from_dict(state.to_dict())
    restored_cat = next(iter(restored.a))
    assert restored_cat.assignment_provenance == "curated-mcp"
    assert restored_cat.risk_ids == ("R001", "R002")


def test_label_state_empty_round_trip() -> None:
    """An empty LabelState round-trips cleanly (default for new sessions)."""
    state = LabelState()
    assert LabelState.from_dict(state.to_dict()).a == frozenset()
    # None input also yields empty (default-tolerant per
    # Constitution §Sec. Constraints).
    assert LabelState.from_dict(None).a == frozenset()
