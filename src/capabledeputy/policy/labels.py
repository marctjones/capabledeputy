"""Information-flow labels (DESIGN.md §7.1; 003 four-axis extension).

The v0.7 flat `Label` enum is retained for backward-compat reads of
sessions on the legacy SCHEMA_VERSION 5 storage shape — but new code
MUST consume the four-axis representation (AxisA / AxisB / AxisC /
AxisD) introduced for v0.9. The legacy enum will be removed at
SCHEMA_VERSION 7 (FR-024 forward-only).

Axis A — Data Category (this file: AxisA, Category schema).
Axis B — Provenance Lattice (this file: AxisB, ProvenanceLevel).
Axis C — Effect Class (lives on ToolDefinition + Capability.kind).
Axis D — Decision Context (policy/axis_d.py: DecisionContext, aliased as AxisD).

Each axis dataclass is `@dataclass(frozen=True)` with default-tolerant
to_dict/from_dict for backward-compat reads (Constitution §Sec.
Constraints). T118 most_restrictive_inherit composes non-enumerated
fields (risk_ids, assignment_provenance) when labels are derived or
delegated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self

from capabledeputy.policy.axis_d import DecisionContext
from capabledeputy.policy.tiers import Tier, max_of


class Label(StrEnum):
    """LEGACY v0.7 flat label set — retained for backward-compat reads
    only. New code uses AxisA/AxisB/AxisD instead. Scheduled for
    removal at SCHEMA_VERSION 7 (FR-024 forward-only)."""

    CONFIDENTIAL_HEALTH = "confidential.health"
    CONFIDENTIAL_FINANCIAL = "confidential.financial"
    CONFIDENTIAL_PERSONAL = "confidential.personal"
    UNTRUSTED_EXTERNAL = "untrusted.external"
    UNTRUSTED_USER_INPUT = "untrusted.user_input"
    TRUSTED_USER_DIRECT = "trusted.user_direct"
    EGRESS_EMAIL = "egress.email"
    EGRESS_PURCHASE = "egress.purchase"


# --- Axis B: Provenance lattice (FR-004) -----------------------------


class ProvenanceLevel(StrEnum):
    """Three-level provenance lattice, monotone order:
    PRINCIPAL_DIRECT > SYSTEM_INTERNAL > EXTERNAL_UNTRUSTED.
    Integrity-floor flag attaches at the AxisB level, not here."""

    PRINCIPAL_DIRECT = "principal-direct"
    SYSTEM_INTERNAL = "system-internal"
    EXTERNAL_UNTRUSTED = "external-untrusted"


_PROVENANCE_RANK: dict[ProvenanceLevel, int] = {
    ProvenanceLevel.PRINCIPAL_DIRECT: 0,  # highest integrity
    ProvenanceLevel.SYSTEM_INTERNAL: 1,
    ProvenanceLevel.EXTERNAL_UNTRUSTED: 2,  # lowest integrity (most-tainted)
}


def provenance_max(*levels: ProvenanceLevel) -> ProvenanceLevel:
    """Most-tainted provenance across the inputs. Fail-closed on
    empty (callers must supply at least one)."""
    if not levels:
        raise ValueError("provenance_max() requires at least one ProvenanceLevel")
    return max(levels, key=lambda lvl: _PROVENANCE_RANK[lvl])


# --- Axis A: Data Category (FR-002, FR-007) --------------------------


class AssignmentProvenance(StrEnum):
    """Where a label assignment came from. The strictest source wins
    on composition (most_restrictive_inherit). Per spec, the
    `raise-only-inspector` provenance is special: it can only ADD
    taint, never CLEAR it — used by the raise-only-inspector hook
    in the dispatcher.
    """

    SYSTEM_DEFAULT = "system-default"
    SOURCE_DECLARED = "source-declared"  # the substrate told us
    CURATED_MCP = "curated-mcp"  # operator-vetted MCP server
    HUMAN_DECLARED = "human-declared"  # the principal said so
    RAISE_ONLY_INSPECTOR = "raise-only-inspector"  # FR-025
    LEGACY_MIGRATION = "legacy-migration"  # v5->v6 backfill
    OPERATOR_DECLARED = "operator-declared"  # binding-resolved


@dataclass(frozen=True)
class CategoryTag:
    """One Axis-A label: (category-id, resolved tier, risk-register ids,
    assignment provenance). 003 redesign canonical leaf type (was
    AxisACategory). Categories are operator-declared in configs/labels.yaml
    and loaded by the resolution layer."""

    category: str
    tier: Tier
    risk_ids: tuple[str, ...] = field(default_factory=tuple)
    assignment_provenance: str = "system-default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "tier": self.tier.value,
            "risk_ids": list(self.risk_ids),
            "assignment_provenance": self.assignment_provenance,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            category=str(d["category"]),
            tier=Tier(d["tier"]),
            risk_ids=tuple(str(r) for r in d.get("risk_ids", [])),
            assignment_provenance=str(d.get("assignment_provenance", "system-default")),
        )


@dataclass(frozen=True)
class AxisA:
    """Session-level Axis A label set: a list of categories with
    their resolved tiers. Empty means 'no labeled categories in
    this session'. Composition with other AxisA values is
    most-restrictive per-category via most_restrictive_inherit."""

    categories: tuple[CategoryTag, ...] = field(default_factory=tuple)

    def to_dict(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.categories]

    @classmethod
    def from_dict(cls, raw: list[dict[str, Any]] | None) -> Self:
        if not raw:
            return cls(categories=())
        return cls(categories=tuple(CategoryTag.from_dict(d) for d in raw))


# --- Axis B: Provenance set + integrity floor (FR-004) --------------


@dataclass(frozen=True)
class ProvenanceTag:
    """One Axis-B label: a provenance level present in the session. 003
    redesign canonical leaf type (was AxisBEntry). NOTE: `integrity_floor`
    is retained transitionally for serialization compatibility; the
    redesign moves the floor to the Operation (`required_floor`) and it
    will be dropped from the data tag in a later R4 step."""

    level: ProvenanceLevel
    integrity_floor: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level.value, "integrity_floor": self.integrity_floor}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            level=ProvenanceLevel(d["level"]),
            integrity_floor=bool(d.get("integrity_floor", False)),
        )


@dataclass(frozen=True)
class AxisB:
    """Session-level Axis B label set: which provenance levels are
    present + whether any step demands an integrity floor."""

    entries: tuple[ProvenanceTag, ...] = field(default_factory=tuple)

    def to_dict(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.entries]

    @classmethod
    def from_dict(cls, raw: list[dict[str, Any]] | None) -> Self:
        if not raw:
            return cls(entries=())
        return cls(entries=tuple(ProvenanceTag.from_dict(d) for d in raw))


# --- 003 redesign: LabelState (the propagating labels) + apply/remove --
#
# LabelState bundles Axis A + Axis B (the only labels that propagate).
# One composition rule (most_restrictive_inherit). Applied by 3 sources
# (bindings / inherent declaration / raise-only inspector); removed only
# by a certified declassifier (a non-declassifier TagTransfer may not
# remove). See specs/003-labeling-framework/label-model-redesign.md.


class LabelError(RuntimeError):
    """An illegal label operation (e.g. a non-declassifier attempting
    removal). Fail-closed per Constitution VI."""


# Authority order for assignment_provenance (strict total order so the
# tie-break in composition is deterministic — SC-002).
_AUTHORITY_RANK: dict[str, int] = {
    "raise-only-inspector": 0,
    "legacy-migration": 1,
    "system-default": 2,
    "source-declared": 3,
    "curated-mcp": 4,
    "operator-declared": 5,
    "human-declared": 6,
}
_PROVENANCE_ORDER: tuple[ProvenanceLevel, ...] = tuple(ProvenanceLevel)


def _authority(prov: str) -> int:
    return _AUTHORITY_RANK.get(prov, _AUTHORITY_RANK["system-default"])


@dataclass(frozen=True)
class LabelState:
    """The propagating label set: Axis A categories + Axis B provenance.
    Empty = unlabeled. Supersedes the separate AxisA+AxisB pair."""

    a: frozenset[CategoryTag] = frozenset()
    b: frozenset[ProvenanceTag] = frozenset()


def _compose_a(*sets: frozenset[CategoryTag]) -> frozenset[CategoryTag]:
    by_cat: dict[str, list[CategoryTag]] = {}
    for tag_set in sets:
        for tag in tag_set:
            by_cat.setdefault(tag.category, []).append(tag)
    out: set[CategoryTag] = set()
    for cat, tags in by_cat.items():
        tier = max_of(*(t.tier for t in tags))
        risks = tuple(sorted({r for t in tags for r in t.risk_ids}))
        prov = max((t.assignment_provenance for t in tags), key=_authority)
        out.add(CategoryTag(category=cat, tier=tier, risk_ids=risks, assignment_provenance=prov))
    return frozenset(out)


def most_restrictive_inherit(*states: LabelState) -> LabelState:
    """The single composition rule: A per-category most-restrictive
    (tier=max, risk_ids=union, provenance=most-authoritative); B=union.
    Never lowers a tier or drops a provenance level."""
    if not states:
        return LabelState()
    a = _compose_a(*(s.a for s in states))
    b: frozenset[ProvenanceTag] = frozenset().union(*(s.b for s in states))
    return LabelState(a=a, b=b)


def meets_required_floor(state: LabelState, required_floor: ProvenanceLevel | None) -> bool:
    """Biba 'no read-down': True iff every provenance level present is at
    least as trustworthy as `required_floor`. None ⇒ no floor ⇒ True."""
    if required_floor is None:
        return True
    floor_rank = _PROVENANCE_ORDER.index(required_floor)
    return all(_PROVENANCE_ORDER.index(t.level) <= floor_rank for t in state.b)


@dataclass(frozen=True)
class TagTransfer:
    """An Operation's effect on the LabelState. `adds` is raised in;
    `removes` is honoured ONLY for a certified declassifier."""

    adds: LabelState = LabelState()
    removes: LabelState | None = None
    is_declassifier: bool = False

    def __post_init__(self) -> None:
        if self.removes is not None and not self.is_declassifier:
            raise LabelError(
                "a non-declassifier TagTransfer may not specify removals (Constitution VI)",
            )


def _remove(state: LabelState, rem: LabelState) -> LabelState:
    rem_cats = {t.category for t in rem.a}
    rem_levels = {t.level for t in rem.b}
    return LabelState(
        a=frozenset(t for t in state.a if t.category not in rem_cats),
        b=frozenset(t for t in state.b if t.level not in rem_levels),
    )


def apply_transfer(state: LabelState, transfer: TagTransfer) -> LabelState:
    """Apply an Operation's tag-transfer. Adds raised in; removals only
    for a certified declassifier, else fail-closed."""
    raised = most_restrictive_inherit(state, transfer.adds)
    if transfer.removes is None:
        return raised
    if not transfer.is_declassifier:
        raise LabelError("only a certified declassifier may remove tags (Constitution VI)")
    return _remove(raised, transfer.removes)


# --- Axis D: Decision Context (FR-006, FR-029, FR-033, FR-037, T136) -------
#
# DecisionContext is the first-class type (policy/axis_d.py).
# AxisD is an alias for backward compatibility with Session serialization.

AxisD = DecisionContext


# --- T118 most_restrictive_inherit (FR-013) -------------------------


def most_restrictive_inherit_axis_a(parent: AxisA, child: AxisA) -> AxisA:
    """Per-category most-restrictive merge of two AxisA sets.

    For each category present in either side: tier = max(parent.tier,
    child.tier); risk_ids = set-union; assignment_provenance = the
    strictest source (parent wins as the more-authoritative source —
    derivation cannot wash provenance away). FR-013 non-enumerated
    inheritance per T118.
    """
    by_category: dict[str, CategoryTag] = {c.category: c for c in parent.categories}
    for cc in child.categories:
        if cc.category not in by_category:
            by_category[cc.category] = cc
            continue
        pc = by_category[cc.category]
        merged_risks = tuple(sorted(set(pc.risk_ids) | set(cc.risk_ids)))
        # Parent's assignment_provenance is the more-authoritative
        # source for derived data; only escalate to child's if child's
        # is "raise-only-inspector" (which only adds taint, never clears).
        merged_provenance = (
            cc.assignment_provenance
            if cc.assignment_provenance == "raise-only-inspector"
            else pc.assignment_provenance
        )
        by_category[cc.category] = CategoryTag(
            category=cc.category,
            tier=max_of(pc.tier, cc.tier),
            risk_ids=merged_risks,
            assignment_provenance=merged_provenance,
        )
    return AxisA(categories=tuple(by_category.values()))


def most_restrictive_inherit_axis_b(parent: AxisB, child: AxisB) -> AxisB:
    """Most-restrictive merge of two AxisB sets: union of provenance
    levels (taint never washes away); integrity_floor=True iff either
    side had it. FR-013 non-enumerated inheritance per T118."""
    by_level: dict[ProvenanceLevel, ProvenanceTag] = {e.level: e for e in parent.entries}
    for ce in child.entries:
        if ce.level not in by_level:
            by_level[ce.level] = ce
            continue
        pe = by_level[ce.level]
        by_level[ce.level] = ProvenanceTag(
            level=ce.level,
            integrity_floor=pe.integrity_floor or ce.integrity_floor,
        )
    return AxisB(entries=tuple(by_level.values()))
