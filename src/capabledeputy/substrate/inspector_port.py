"""Raise-Only Inspector port (003 T121, FR-025).

A RaiseOnlyInspector reads a freshly-ingested value plus its current
labels and may return a taint-raising delta. It MUST NOT clear taint:
the contract is monotone-only. The runtime composes any returned
delta via most_restrictive_inherit (T118) at the ingest hook.

No provider impl in 003 — that's spec 004. This file defines the port
shape so the rule that "inspectors raise, never lower" is structural,
not just documented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from capabledeputy.policy.labels import AxisA, AxisB


@dataclass(frozen=True)
class InspectorDelta:
    """A taint-raising delta returned by a RaiseOnlyInspector. Empty
    deltas (no axes set) are valid and mean 'nothing to raise'.
    The runtime composes via most_restrictive_inherit, which is
    monotone-only — so an inspector can only ever raise, even if it
    were to attempt to lower (the composition would discard the
    lowering)."""

    axis_a_raise: AxisA = field(default_factory=AxisA)
    axis_b_raise: AxisB = field(default_factory=AxisB)


class RaiseOnlyInspector(Protocol):
    """Inspector contract. Implementations are out of scope for 003
    (deferred to spec 004 substrate track). The Protocol exists so
    the ingest hook can be typed and the rule expressed structurally.
    Implementations MUST treat the contract as strict — any lowering
    behavior is a Principle-VI violation, even if the runtime's
    composition would catch it."""

    def inspect(
        self,
        *,
        value: object,
        current_axis_a: AxisA,
        current_axis_b: AxisB,
    ) -> InspectorDelta:
        """Return a delta whose axes may raise (add categories,
        provenance levels, integrity-floor) but MUST NOT lower."""
        ...
