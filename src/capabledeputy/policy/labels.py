"""Information-flow labels (DESIGN.md §7.1; 003 four-axis extension).

New code MUST consume the four-axis representation (AxisC / AxisD) and the
bundled two-axis propagating label representation (LabelState) introduced
for v0.9. SCHEMA_VERSION 7 and forward use only four-axis representations;
the v0.7 flat Label enum (and backward-compat converters) have been removed
(FR-024 forward-only).

Axis A — Data Category (this file: CategoryTag schema).
Axis B — Provenance Lattice (this file: ProvenanceTag).
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
from pathlib import Path
from typing import Any, Self

from capabledeputy.policy.axis_d import DecisionContext
from capabledeputy.policy.tiers import Tier, max_of

# --- Axis B: Provenance lattice (FR-004) -----------------------------


class ProvenanceLevel(StrEnum):
    """Three-level provenance lattice, monotone order:
    PRINCIPAL_DIRECT > SYSTEM_INTERNAL > EXTERNAL_UNTRUSTED.
    The integrity floor is a property of the Operation
    (`Operation.required_floor`), not of the provenance level."""

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


# --- Axis B: Provenance set + integrity floor (FR-004) --------------


@dataclass(frozen=True)
class ProvenanceTag:
    """One Axis-B label: a provenance level present in the session. 003
    redesign canonical leaf type (was AxisBEntry). R4b.4: integrity_floor
    removed (moved to Operation.required_floor)."""

    level: ProvenanceLevel

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            level=ProvenanceLevel(d["level"]),
        )


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for storage/serialization.

        Format: {"a": [CategoryTag.to_dict() for each], "b": [ProvenanceTag.to_dict() for each]}
        """
        return {
            "a": [t.to_dict() for t in sorted(self.a, key=lambda x: x.category)],
            "b": [t.to_dict() for t in sorted(self.b, key=lambda x: x.level.value)],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> LabelState:
        """Deserialize from a dict. Default-tolerant: missing keys or None become
        empty LabelState()."""
        if d is None:
            return cls()
        a_list = d.get("a", [])
        b_list = d.get("b", [])
        a = frozenset(CategoryTag.from_dict(x) for x in a_list) if a_list else frozenset()
        b = frozenset(ProvenanceTag.from_dict(x) for x in b_list) if b_list else frozenset()
        return cls(a=a, b=b)


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


def inherit(parent: LabelState, child: LabelState) -> LabelState:
    """**Directional** parent→child inheritance (delegation / fork /
    derivation) — distinct from the symmetric `most_restrictive_inherit`
    (in-session accumulation). Per-category: tier = max, risk_ids = union,
    and `assignment_provenance` stays the **parent's** ("derivation cannot
    launder provenance away" — a materialized provenance / FR-022 property),
    *unless* the child's is `raise-only-inspector` (which only adds taint).
    Axis B: union of levels."""
    by_cat: dict[str, CategoryTag] = {t.category: t for t in parent.a}
    for cc in child.a:
        pc = by_cat.get(cc.category)
        if pc is None:
            by_cat[cc.category] = cc
            continue
        prov = (
            cc.assignment_provenance
            if cc.assignment_provenance == "raise-only-inspector"
            else pc.assignment_provenance
        )
        by_cat[cc.category] = CategoryTag(
            category=cc.category,
            tier=max_of(pc.tier, cc.tier),
            risk_ids=tuple(sorted(set(pc.risk_ids) | set(cc.risk_ids))),
            assignment_provenance=prov,
        )
    by_lvl: dict[ProvenanceLevel, ProvenanceTag] = {e.level: e for e in parent.b}
    for ce in child.b:
        if ce.level not in by_lvl:
            by_lvl[ce.level] = ce
    return LabelState(a=frozenset(by_cat.values()), b=frozenset(by_lvl.values()))


# --- Apply source #2: operation/tool inherent declaration (FR-013, §R5) --
#
# Conversion from legacy flat-label string values to the new `LabelState`
# representation. `EGRESS_*` are Axis-C **effects**, not propagating tags
# (the un-fusing), so they map to the empty state.
#
# Issue #50 — catalog-aware tier resolution. The flat-string path
# (servers.d config, cross-host fallback, legacy bundles) must carry the
# *real* tier from configs/labels.yaml, not a flattened REGULATED — a
# lossy tier weakens BLP clearance checks and the envelope dial for every
# label that arrives this way. We resolve each category's `default_tier`
# from the catalog, cached on first use, and fail-safe to REGULATED when
# the catalog is absent / unparseable / silent on a category.
_CONFIDENTIAL_LABEL_CATEGORY: dict[str, str] = {
    "confidential.health": "health",
    "confidential.financial": "financial",
    "confidential.personal": "personal",
}
_CATEGORY_TIER_CACHE: dict[str, Tier] | None = None


def _category_default_tiers() -> dict[str, Tier]:
    """Load category→default_tier from configs/labels.yaml (Issue #50).

    Configs-dir resolution mirrors the daemon's (`CAPDEP_CONFIGS_DIR`
    env, else `configs/`). Inlined rather than importing the daemon so
    the policy layer keeps no upward dependency. Fail-safe to an empty
    map (callers then fall back to REGULATED)."""
    import os

    from capabledeputy.policy.resolution import ResolutionError, load_categories

    base = os.environ.get("CAPDEP_CONFIGS_DIR")
    path = (Path(base) if base else Path("configs")) / "labels.yaml"
    try:
        cats = load_categories(path)
    except (ResolutionError, OSError):
        return {}
    return {cid: c.default_tier for cid, c in cats.items()}


def _category_tier(category: str, default: Tier = Tier.REGULATED) -> Tier:
    global _CATEGORY_TIER_CACHE
    if _CATEGORY_TIER_CACHE is None:
        _CATEGORY_TIER_CACHE = _category_default_tiers()
    return _CATEGORY_TIER_CACHE.get(category, default)


def reset_category_tier_cache() -> None:
    """Drop the cached catalog tiers (tests / operator config reload)."""
    global _CATEGORY_TIER_CACHE
    _CATEGORY_TIER_CACHE = None


def legacy_label_strings_to_tags() -> dict[str, LabelState]:
    """Map each legacy flat-label string to the `LabelState` it denotes.

    confidential.* tiers are resolved from the category catalog
    (Issue #50); provenance/egress entries are catalog-independent.
    Built fresh per call but backed by the tier cache, so it stays cheap
    to call repeatedly."""
    tags: dict[str, LabelState] = {
        label: LabelState(
            a=frozenset(
                {
                    CategoryTag(
                        category,
                        _category_tier(category),
                        assignment_provenance="source-declared",
                    ),
                },
            ),
        )
        for label, category in _CONFIDENTIAL_LABEL_CATEGORY.items()
    }
    tags.update(
        {
            "untrusted.external": LabelState(
                b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
            ),
            "untrusted.user_input": LabelState(
                b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
            ),
            "trusted.user_direct": LabelState(
                b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)}),
            ),
            "egress.email": LabelState(),  # Axis-C effect, not a propagating tag
            "egress.purchase": LabelState(),
        },
    )
    return tags


def tags_for_label_string(label_str: str) -> LabelState:
    """The propagating `LabelState` a legacy flat-label string denotes."""
    return legacy_label_strings_to_tags().get(label_str, LabelState())


def legacy_labels_present(state: LabelState) -> list[str]:
    """Reverse of `legacy_label_strings_to_tags`: the legacy flat strings a
    `LabelState` carries, for backward-compatible serialization to clients.

    Matched by Axis-A **category** and Axis-B provenance **level** only —
    NOT by tier or assignment-provenance metadata. The legacy strings
    (e.g. `confidential.health`) encode just the category, so a health tag
    serializes to `confidential.health` whatever its resolved tier
    (Issue #50: tiers now come from the catalog, so a tier-sensitive match
    would spuriously drop labels). Egress entries are effects, not
    propagating tags, so they never appear. Sorted for stable output."""
    cats = {t.category for t in state.a}
    levels = {t.level for t in state.b}
    present: list[str] = []
    for label_str, tags in legacy_label_strings_to_tags().items():
        if not tags.a and not tags.b:
            continue
        if all(c.category in cats for c in tags.a) and all(p.level in levels for p in tags.b):
            present.append(label_str)
    return sorted(present)


def tags_for_labels_strings(labels: frozenset[str]) -> LabelState:
    """Compose the four-axis taint denoted by a flat label string set. Empty
    set ⇒ empty state. The result is the apply-source-#2 delta the
    dispatch chokepoint raises into the session's `LabelState`."""
    if not labels:
        return LabelState()
    return most_restrictive_inherit(*(tags_for_label_string(label) for label in labels))


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
