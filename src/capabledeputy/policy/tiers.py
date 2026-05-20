"""Sensitivity tiers (003 FR-027).

A strict total order over five levels:

    none < sensitive < regulated < restricted < prohibited

Frozen as a module-level enum. The `decide()` resolver consults this
order at multiple points: most-restrictive composition (FR-026(a)),
max-tier-clearance enforcement (FR-008), and outcome-envelope
selection (FR-030). All comparisons are deterministic and LLM-isolated.
"""

from __future__ import annotations

from enum import StrEnum


class Tier(StrEnum):
    """Sensitivity tier (FR-027). Total order: NONE < SENSITIVE <
    REGULATED < RESTRICTED < PROHIBITED. See _RANK for the strict
    integer mapping used by the comparison helpers."""

    NONE = "none"
    SENSITIVE = "sensitive"
    REGULATED = "regulated"
    RESTRICTED = "restricted"
    PROHIBITED = "prohibited"


_RANK: dict[Tier, int] = {
    Tier.NONE: 0,
    Tier.SENSITIVE: 1,
    Tier.REGULATED: 2,
    Tier.RESTRICTED: 3,
    Tier.PROHIBITED: 4,
}


def compare(a: Tier, b: Tier) -> int:
    """Return -1 if a < b, 0 if equal, 1 if a > b (strict total order)."""
    ra, rb = _RANK[a], _RANK[b]
    if ra < rb:
        return -1
    if ra > rb:
        return 1
    return 0


def is_above(a: Tier, b: Tier) -> bool:
    """True iff a is strictly more restrictive than b."""
    return _RANK[a] > _RANK[b]


def max_of(*tiers: Tier) -> Tier:
    """Most-restrictive tier across the inputs. Raises on empty input —
    callers must supply at least one Tier (fail-closed; absent input
    should never silently degrade to NONE)."""
    if not tiers:
        raise ValueError("max_of() requires at least one Tier")
    return max(tiers, key=lambda t: _RANK[t])
