"""Information-flow labels (DESIGN.md §7.1; 003 four-axis extension).

The v0.7 flat `Label` enum is retained for backward-compat reads of
sessions on the legacy SCHEMA_VERSION 5 storage shape — but new code
MUST consume the four-axis representation (AxisA / AxisB / AxisC /
AxisD) introduced for v0.9. The legacy enum will be removed at
SCHEMA_VERSION 7 (FR-024 forward-only).

Axis A — Data Category (this file: AxisA, Category schema).
Axis B — Provenance Lattice (this file: AxisB, ProvenanceLevel).
Axis C — Effect Class (lives on ToolDefinition + Capability.kind).
Axis D — Decision Context (this file: AxisD).

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
class AxisACategory:
    """One entry in a session's Axis A label set: (category-id,
    resolved tier, risk-register ids, assignment provenance).
    Categories themselves are operator-declared in configs/labels.yaml
    and loaded by US1's resolution layer."""

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

    categories: tuple[AxisACategory, ...] = field(default_factory=tuple)

    def to_dict(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.categories]

    @classmethod
    def from_dict(cls, raw: list[dict[str, Any]] | None) -> Self:
        if not raw:
            return cls(categories=())
        return cls(categories=tuple(AxisACategory.from_dict(d) for d in raw))


# --- Axis B: Provenance set + integrity floor (FR-004) --------------


@dataclass(frozen=True)
class AxisBEntry:
    """One per provenance level present in this session. The
    integrity_floor flag indicates whether *this step* must reject
    inputs below the floor (Biba direction; FR-004)."""

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

    entries: tuple[AxisBEntry, ...] = field(default_factory=tuple)

    def to_dict(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.entries]

    @classmethod
    def from_dict(cls, raw: list[dict[str, Any]] | None) -> Self:
        if not raw:
            return cls(entries=())
        return cls(entries=tuple(AxisBEntry.from_dict(d) for d in raw))


# --- Axis D: Decision Context (FR-006, FR-029) ----------------------


@dataclass(frozen=True)
class AxisD:
    """Per-session decision context.

    `reversibility` is a dict `{degree, agent}` for now; the proper
    ReversibilityLabel type lands in US6 (policy/reversibility.py).
    Storing as dict here keeps the schema stable across phases.
    """

    initiator: str = "unset"
    authentication: str = "none"
    counterparty: str | None = None
    relationship_group_ids: tuple[str, ...] = field(default_factory=tuple)
    expectedness: str = "anomalous"
    reversibility: dict[str, str] = field(
        default_factory=lambda: {"degree": "irreversible", "agent": "external"},
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "initiator": self.initiator,
            "authentication": self.authentication,
            "counterparty": self.counterparty,
            "relationship_group_ids": list(self.relationship_group_ids),
            "expectedness": self.expectedness,
            "reversibility": dict(self.reversibility),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Self:
        if not d:
            return cls()
        rev = d.get("reversibility") or {"degree": "irreversible", "agent": "external"}
        return cls(
            initiator=str(d.get("initiator", "unset")),
            authentication=str(d.get("authentication", "none")),
            counterparty=d.get("counterparty"),
            relationship_group_ids=tuple(str(g) for g in d.get("relationship_group_ids", [])),
            expectedness=str(d.get("expectedness", "anomalous")),
            reversibility={
                "degree": str(rev.get("degree", "irreversible")),
                "agent": str(rev.get("agent", "external")),
            },
        )


# --- T118 most_restrictive_inherit (FR-013) -------------------------


def most_restrictive_inherit_axis_a(parent: AxisA, child: AxisA) -> AxisA:
    """Per-category most-restrictive merge of two AxisA sets.

    For each category present in either side: tier = max(parent.tier,
    child.tier); risk_ids = set-union; assignment_provenance = the
    strictest source (parent wins as the more-authoritative source —
    derivation cannot wash provenance away). FR-013 non-enumerated
    inheritance per T118.
    """
    by_category: dict[str, AxisACategory] = {c.category: c for c in parent.categories}
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
        by_category[cc.category] = AxisACategory(
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
    by_level: dict[ProvenanceLevel, AxisBEntry] = {e.level: e for e in parent.entries}
    for ce in child.entries:
        if ce.level not in by_level:
            by_level[ce.level] = ce
            continue
        pe = by_level[ce.level]
        by_level[ce.level] = AxisBEntry(
            level=ce.level,
            integrity_floor=pe.integrity_floor or ce.integrity_floor,
        )
    return AxisB(entries=tuple(by_level.values()))
