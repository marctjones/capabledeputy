"""The propagating label model (003 redesign).

A session accumulates exactly two kinds of label: Axis A (data
`category x tier`) and Axis B (`provenance`). Together they are the
`LabelState` — the taint that flows along a session. Axis C (Operation /
effect_class) and Axis D (decision context) are NOT labels and do not
live here.

Lifecycle (see specs/003-labeling-framework/label-model-redesign.md §4):
  - APPLY (raise/add only): three sources — binding resolution, operation
    inherent declaration, raise-only inspectors. Composition is one rule,
    `most_restrictive_inherit`.
  - REMOVE (downgrade/clear): exactly one source — a certified
    declassifier. A non-declassifier `TagTransfer` may not specify
    removals (fail-closed, Constitution VI).

Landed in R1 with property tests; consumers migrate in R3/R4 and the
legacy flat `Label` enum is deleted in R7.
"""

from __future__ import annotations

from dataclasses import dataclass

from capabledeputy.policy.labels import AssignmentProvenance, ProvenanceLevel
from capabledeputy.policy.tiers import Tier, max_of


class LabelError(RuntimeError):
    """An illegal label operation (e.g. a non-declassifier attempting
    removal). Fail-closed per Constitution VI."""


# Authority order for `assigned_by` (higher = more authoritative for the
# tier value). A raise-only inspector is the weakest: it may tighten but
# is never the authoritative source. This MUST be a strict total order
# (no ties): `_max_authority` is a deterministic tie-break used during
# composition, so equal ranks would make compose order-dependent and
# break SC-002 determinism.
_AUTHORITY_RANK: dict[AssignmentProvenance, int] = {
    AssignmentProvenance.RAISE_ONLY_INSPECTOR: 0,
    AssignmentProvenance.LEGACY_MIGRATION: 1,
    AssignmentProvenance.SYSTEM_DEFAULT: 2,
    AssignmentProvenance.SOURCE_DECLARED: 3,
    AssignmentProvenance.CURATED_MCP: 4,
    AssignmentProvenance.OPERATOR_DECLARED: 5,
    AssignmentProvenance.HUMAN_DECLARED: 6,
}

# Integrity order (index = rank): principal-direct(0, highest integrity)
# > system-internal(1) > external-untrusted(2, lowest). Derived from enum
# definition order so it cannot drift from the canonical lattice.
_PROVENANCE_ORDER: tuple[ProvenanceLevel, ...] = tuple(ProvenanceLevel)


def _max_authority(*provs: AssignmentProvenance) -> AssignmentProvenance:
    return max(provs, key=lambda p: _AUTHORITY_RANK[p])


@dataclass(frozen=True)
class CategoryTag:
    """One Axis-A label: a data category, its resolved tier, the risk-
    register ids it carries, and how the assignment got here."""

    category: str
    tier: Tier
    risk_ids: frozenset[str] = frozenset()
    assigned_by: AssignmentProvenance = AssignmentProvenance.SYSTEM_DEFAULT


@dataclass(frozen=True)
class ProvenanceTag:
    """One Axis-B label: a provenance level present in the session.
    The integrity *floor* is NOT here — it is an Operation requirement
    (`effect_class.Operation.required_floor`)."""

    level: ProvenanceLevel


@dataclass(frozen=True)
class LabelState:
    """The propagating label set: Axis A + Axis B. Empty = unlabeled."""

    a: frozenset[CategoryTag] = frozenset()
    b: frozenset[ProvenanceTag] = frozenset()


def _compose_a(*sets: frozenset[CategoryTag]) -> frozenset[CategoryTag]:
    """Per-category most-restrictive merge: tier = max, risk_ids = union,
    assigned_by = most-authoritative. Each reduction is associative and
    commutative, so the whole compose is order-independent (SC-002)."""
    by_cat: dict[str, list[CategoryTag]] = {}
    for tag_set in sets:
        for tag in tag_set:
            by_cat.setdefault(tag.category, []).append(tag)
    out: set[CategoryTag] = set()
    for cat, tags in by_cat.items():
        tier = max_of(*(t.tier for t in tags))
        risks: frozenset[str] = frozenset().union(*(t.risk_ids for t in tags))
        prov = _max_authority(*(t.assigned_by for t in tags))
        out.add(CategoryTag(category=cat, tier=tier, risk_ids=risks, assigned_by=prov))
    return frozenset(out)


def most_restrictive_inherit(*states: LabelState) -> LabelState:
    """The single composition rule. A is per-category most-restrictive;
    B is set-union (every provenance level present is retained — the
    most-tainted dominates downstream). Never lowers a tier or drops a
    provenance level."""
    if not states:
        return LabelState()
    a = _compose_a(*(s.a for s in states))
    b: frozenset[ProvenanceTag] = frozenset().union(*(s.b for s in states))
    return LabelState(a=a, b=b)


def meets_required_floor(state: LabelState, required_floor: ProvenanceLevel | None) -> bool:
    """Biba "no read-down": True iff every provenance level present is at
    least as trustworthy as `required_floor`. `None` ⇒ no floor ⇒ True."""
    if required_floor is None:
        return True
    floor_rank = _PROVENANCE_ORDER.index(required_floor)
    return all(_PROVENANCE_ORDER.index(tag.level) <= floor_rank for tag in state.b)


@dataclass(frozen=True)
class TagTransfer:
    """An Operation's effect on the LabelState (CORE tag-transfer).

    `adds` is unioned in (raise-only). `removes` is honoured ONLY when
    `is_declassifier` is True — a non-declassifier transfer with removals
    is rejected at construction (fail-closed, Constitution VI)."""

    adds: LabelState = LabelState()
    removes: LabelState | None = None
    is_declassifier: bool = False

    def __post_init__(self) -> None:
        if self.removes is not None and not self.is_declassifier:
            raise LabelError(
                "a non-declassifier TagTransfer may not specify removals "
                "(only certified declassifiers may remove tags; Constitution VI)",
            )


def _remove(state: LabelState, rem: LabelState) -> LabelState:
    rem_cats = {t.category for t in rem.a}
    rem_levels = {t.level for t in rem.b}
    return LabelState(
        a=frozenset(t for t in state.a if t.category not in rem_cats),
        b=frozenset(t for t in state.b if t.level not in rem_levels),
    )


def apply_transfer(state: LabelState, transfer: TagTransfer) -> LabelState:
    """Apply an Operation's tag-transfer. Adds are raised in
    (most-restrictive); removals happen only for a certified
    declassifier, else fail-closed."""
    raised = most_restrictive_inherit(state, transfer.adds)
    if transfer.removes is None:
        return raised
    if not transfer.is_declassifier:  # defense in depth; __post_init__ also guards
        raise LabelError("only a certified declassifier may remove tags (Constitution VI)")
    return _remove(raised, transfer.removes)
