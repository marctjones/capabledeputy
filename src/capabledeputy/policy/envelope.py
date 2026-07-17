"""Outcome Envelopes + Risk-Preference dial (003 US6 / FR-030).

Each (category x effect x decision-context x reversibility) cell
declares a `{strictest, loosest}` envelope of outcomes. The
operator's risk-preference dial selects a single point inside each
cell's envelope — but never crosses a hard floor (`strictest`).

A "hard-floor cell" has a degenerate envelope: `strictest == loosest`.
The dial can do nothing there; the outcome is operator-locked
regardless of preference. SC-010 invariant: every dial value picks
an outcome within the declared envelope; hard-floor cells are
immovable.

Loading:
  - envelopes.yaml supplies the cell envelopes.
  - risk_preference.json supplies the dial value
    (cautious / balanced / permissive).
Both are operator-curated and AI-read-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from capabledeputy.policy.config_format import resolve_config_path as _resolve_config_path
from capabledeputy.policy.decision_rules import RuleOutcome


class EnvelopeError(RuntimeError):
    """envelopes.yaml / risk_preference.json is malformed; fail-closed
    per Principle VI."""


class RiskPreference(StrEnum):
    """Operator-set dial. Order is cautious < balanced < permissive
    (increasing autonomy toward the cell's loosest outcome). Never
    crosses a hard floor."""

    CAUTIOUS = "cautious"
    BALANCED = "balanced"
    PERMISSIVE = "permissive"


# Order outcomes from most-restrictive to least-restrictive.
_OUTCOME_ORDER: list[RuleOutcome] = [
    RuleOutcome.DENY,
    RuleOutcome.REQUIRE_APPROVAL,
    RuleOutcome.SUGGEST,
    RuleOutcome.AUTO,
]
_OUTCOME_RANK: dict[RuleOutcome, int] = {o: i for i, o in enumerate(_OUTCOME_ORDER)}


@dataclass(frozen=True)
class CellKey:
    """Compound key identifying an envelope cell. Decision_context
    is a canonicalized Axis-D form — operator-supplied so it's
    stable across loads."""

    category: str
    effect: str
    decision_context_canonical: str
    reversibility: str  # the degree string from ReversibilityDegree

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "effect": self.effect,
            "decision_context_canonical": self.decision_context_canonical,
            "reversibility": self.reversibility,
        }


@dataclass(frozen=True)
class OutcomeEnvelope:
    """One cell's `{strictest, loosest}` envelope. `strictest` is the
    hard floor — even a permissive dial cannot move below it. A
    degenerate envelope (`strictest == loosest`) is immovable."""

    cell: CellKey
    strictest: RuleOutcome
    loosest: RuleOutcome

    def __post_init__(self) -> None:
        if _OUTCOME_RANK[self.strictest] > _OUTCOME_RANK[self.loosest]:
            raise EnvelopeError(
                f"envelope for {self.cell}: strictest={self.strictest.value} "
                f"is less restrictive than loosest={self.loosest.value}",
            )

    @property
    def is_hard_floor(self) -> bool:
        return self.strictest == self.loosest

    def select(self, dial: RiskPreference) -> RuleOutcome:
        """Pick an outcome inside this envelope for the given dial.

        Mapping:
          - cautious   ⇒ strictest
          - balanced   ⇒ the middle outcome (if available) else strictest
          - permissive ⇒ loosest

        Hard-floor cells return `strictest` regardless of the dial —
        SC-010 invariant. The dial never crosses the floor.
        """
        if self.is_hard_floor:
            return self.strictest
        if dial is RiskPreference.CAUTIOUS:
            return self.strictest
        if dial is RiskPreference.PERMISSIVE:
            return self.loosest
        # Balanced: pick the outcome whose rank is halfway between
        # strictest and loosest (rounded toward stricter — when in
        # doubt, ratchet stricter).
        lo = _OUTCOME_RANK[self.strictest]
        hi = _OUTCOME_RANK[self.loosest]
        midpoint = lo + (hi - lo) // 2
        return _OUTCOME_ORDER[midpoint]


@dataclass(frozen=True)
class EnvelopeSet:
    """Loaded envelope catalogue keyed by CellKey."""

    by_cell: dict[CellKey, OutcomeEnvelope] = field(default_factory=dict)

    def lookup(self, cell: CellKey) -> OutcomeEnvelope | None:
        return self.by_cell.get(cell)


@dataclass(frozen=True)
class RiskPreferenceProfile:
    """Owner-set risk-preference profile. Stored unchanged on the
    session at spawn (`sessions.risk_preference_at_spawn`) so replay
    is deterministic regardless of subsequent dial changes."""

    value: RiskPreference
    version: int
    signature: str | None = None


def load_envelopes(path: Path) -> EnvelopeSet:
    """Load configs/envelopes.yaml. Fail-closed on missing/unparseable.
    Empty `envelopes:` permitted — yields a set with no cells (every
    lookup misses, callers fall back to never-auto)."""
    if not path.is_file():
        raise EnvelopeError(f"envelopes config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise EnvelopeError(f"unparseable: {path} — {e}") from e
    if data is None:
        return EnvelopeSet(by_cell={})
    raw = data.get("envelopes") or []
    if not isinstance(raw, list):
        raise EnvelopeError(f"'envelopes' must be a list: {path}")
    by_cell: dict[CellKey, OutcomeEnvelope] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EnvelopeError(f"envelopes[{i}] is not an object")
        try:
            cell_raw = item["cell"]
            if not isinstance(cell_raw, dict):
                raise EnvelopeError(f"envelopes[{i}].cell must be an object")
            cell = CellKey(
                category=str(cell_raw["category"]),
                effect=str(cell_raw["effect"]),
                decision_context_canonical=str(cell_raw["decision_context_canonical"]),
                reversibility=str(cell_raw["reversibility"]),
            )
            envelope = OutcomeEnvelope(
                cell=cell,
                strictest=RuleOutcome(str(item["strictest"])),
                loosest=RuleOutcome(str(item["loosest"])),
            )
        except (KeyError, ValueError) as e:
            raise EnvelopeError(f"envelopes[{i}]: {e}") from e
        if cell in by_cell:
            raise EnvelopeError(f"envelopes[{i}] duplicate cell: {cell}")
        by_cell[cell] = envelope
    return EnvelopeSet(by_cell=by_cell)


def load_risk_preference(path: Path) -> RiskPreferenceProfile:
    """Load the risk-preference profile. Fail-closed on missing/unparseable. The
    `value` field is required and must be one of cautious/balanced/permissive.

    #384 — format-agnostic: the body is parsed as YAML (a JSON superset), so the
    file may be either `.json` (legacy) or `.yaml` (the single-format target). If
    the given path is absent, its `.yaml`/`.json` sibling is tried, so operators
    can migrate the file without touching call sites."""
    path = _resolve_config_path(path)
    if not path.is_file():
        raise EnvelopeError(f"risk_preference config missing: {path}")
    try:
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise EnvelopeError(f"unparseable: {path} — {e}") from e
    if not isinstance(data, dict):
        raise EnvelopeError(f"risk_preference config must be a mapping: {path}")
    try:
        value = RiskPreference(str(data["value"]))
    except (KeyError, ValueError) as e:
        raise EnvelopeError(f"risk_preference 'value' field: {e}") from e
    version = int(data.get("version", 0))
    sig = data.get("signature")
    return RiskPreferenceProfile(
        value=value,
        version=version,
        signature=str(sig) if sig is not None else None,
    )
