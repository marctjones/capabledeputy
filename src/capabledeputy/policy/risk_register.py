"""Risk Register (003 FR-015, FR-028).

A single in-repo JSON file at configs/risk_register.json (operator-
editable, human-declared, AI-read-only) holding entries
`{id, summary, framework_refs[]}`. Labels and decisions cite `id`;
the register itself cites external framework references (NIST CSF,
ISO 27001, OWASP, CIS, etc.) — every register id MUST cite >=1
external ref (SC-001 lint, scripts/lint_risk_register.py).

Loaded once at daemon startup; held in memory. Lookup/exists are O(1)
dict access. The orphan audit is a static check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class RiskRegisterError(RuntimeError):
    """The register file is missing, unparseable, or malformed.
    Fail-closed per Constitution VI — daemon refuses to start."""


@dataclass(frozen=True)
class Threshold:
    """Quantified residual-risk threshold for a risk-register entry.

    Shape depends on the framework reference:
      - FAIR: `magnitude_band_min` in {M1, M2, M3, M4, M5}
      - NIST-AI-RMF: `impact_tier_min` in {tier-1, tier-2, tier-3, tier-4}
      - EU-AI-Act: `risk_class_min` in {prohibited, high, limited, minimal}
      - FIPS-199: `impact_min` in {low, moderate, high}
      - OWASP-RiskRating: `severity_min` in {critical, high, medium, low}

    Per FR-016 / Q5 (2026-05-25): each threshold is bound to an external
    framework so residual-risk decisions remain deterministic and framework-
    traceable. Pure-function threshold checks (Constitution Principle I).
    """

    framework: str
    # Discriminated union: exactly one of these is present depending on framework.
    magnitude_band_min: str | None = None
    impact_tier_min: str | None = None
    risk_class_min: str | None = None
    impact_min: str | None = None
    severity_min: str | None = None


@dataclass(frozen=True)
class RiskRegisterEntry:
    id: str
    summary: str
    framework_refs: tuple[str, ...] = field(default_factory=tuple)
    threshold: Threshold | None = None


@dataclass(frozen=True)
class RiskRegister:
    """In-memory view of configs/risk_register.json. Construct via
    `load()` — the constructor is unguarded for testability."""

    entries: dict[str, RiskRegisterEntry]

    def get(self, register_id: str) -> RiskRegisterEntry:
        try:
            return self.entries[register_id]
        except KeyError as e:
            raise RiskRegisterError(f"unknown risk-register id: {register_id!r}") from e

    def exists(self, register_id: str) -> bool:
        return register_id in self.entries

    def audit_orphans(self) -> list[str]:
        """Return ids with empty framework_refs (SC-001 violation).
        Surfaced by scripts/lint_risk_register.py at CI time; usable
        from runtime audits too (FR-028)."""
        return sorted(rid for rid, entry in self.entries.items() if not entry.framework_refs)

    def get_threshold(self, risk_id: str) -> Threshold | None:
        """Look up the threshold for a risk-register entry.
        Returns None if the entry has no threshold declared.
        Raises RiskRegisterError if the risk_id is unknown.

        Pure function per Constitution Principle I — no side effects,
        deterministic, no LLM input.
        """
        entry = self.get(risk_id)
        return entry.threshold

    def threshold_crossed(
        self,
        risk_id: str,
        decision_residual: dict[str, Any],
    ) -> bool:
        """Check if the residual-risk metrics in `decision_residual`
        exceed the threshold declared for `risk_id` (if any).

        `decision_residual` is a dict of framework-keyed residual metrics,
        e.g., {
            'NIST-AI-RMF': {'impact_tier': 'tier-2'},
            'FAIR': {'magnitude_band': 'M3'},
        }

        Returns True iff the entry has a threshold AND the residual exceeds it.
        Returns False if the entry has no threshold (unmonitored risk).
        Raises RiskRegisterError if risk_id is unknown.

        Pure function per Constitution Principle I.
        """
        threshold = self.get_threshold(risk_id)
        if threshold is None:
            return False

        # Extract the residual metric matching the threshold's framework.
        # If the decision_residual doesn't have the framework key, return False
        # (the decision doesn't cite this framework, so no crossing).
        residual_for_framework = decision_residual.get(threshold.framework)
        if residual_for_framework is None:
            return False

        # Framework-specific threshold comparisons.
        # Each compares a declared threshold minimum to the residual value.

        if threshold.framework == "NIST-AI-RMF":
            # impact_tier_min: tier-1 < tier-2 < tier-3 < tier-4
            # Crossed iff residual tier >= threshold tier
            tier_order = {"tier-1": 1, "tier-2": 2, "tier-3": 3, "tier-4": 4}
            residual_tier = residual_for_framework.get("impact_tier")
            threshold_min = threshold.impact_tier_min
            if residual_tier and threshold_min:
                return tier_order.get(residual_tier, 0) >= tier_order.get(threshold_min, 0)

        elif threshold.framework == "FAIR":
            # magnitude_band_min: M1 < M2 < M3 < M4 < M5
            # Crossed iff residual band >= threshold band
            band_order = {"M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5}
            residual_band = residual_for_framework.get("magnitude_band")
            threshold_min = threshold.magnitude_band_min
            if residual_band and threshold_min:
                return band_order.get(residual_band, 0) >= band_order.get(threshold_min, 0)

        elif threshold.framework == "EU-AI-Act":
            # risk_class_min: minimal < limited < high < prohibited
            # Crossed iff residual class >= threshold class
            class_order = {"minimal": 1, "limited": 2, "high": 3, "prohibited": 4}
            residual_class = residual_for_framework.get("risk_class")
            threshold_min = threshold.risk_class_min
            if residual_class and threshold_min:
                return class_order.get(residual_class, 0) >= class_order.get(threshold_min, 0)

        elif threshold.framework == "FIPS-199":
            # impact_min: low < moderate < high
            # Crossed iff residual impact >= threshold impact
            impact_order = {"low": 1, "moderate": 2, "high": 3}
            residual_impact = residual_for_framework.get("impact")
            threshold_min = threshold.impact_min
            if residual_impact and threshold_min:
                return impact_order.get(residual_impact, 0) >= impact_order.get(
                    threshold_min, 0
                )

        elif threshold.framework == "OWASP-RiskRating":
            # severity_min: low < medium < high < critical
            # Crossed iff residual severity >= threshold severity
            severity_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
            residual_severity = residual_for_framework.get("severity")
            threshold_min = threshold.severity_min
            if residual_severity and threshold_min:
                return severity_order.get(residual_severity, 0) >= severity_order.get(
                    threshold_min, 0
                )

        # Unknown framework or missing data in residual — fail-closed.
        return False

    def __len__(self) -> int:
        return len(self.entries)


def load(path: Path) -> RiskRegister:
    """Load configs/risk_register.json. Fail-closed on missing file,
    malformed JSON, or malformed entry shape."""
    if not path.is_file():
        raise RiskRegisterError(f"risk register missing: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RiskRegisterError(f"risk register unparseable: {path} — {e}") from e

    if not isinstance(raw, dict):
        raise RiskRegisterError(f"risk register root must be an object: {path}")
    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise RiskRegisterError(f"risk register 'entries' must be a list: {path}")

    parsed: dict[str, RiskRegisterEntry] = {}
    for i, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise RiskRegisterError(f"risk register entry {i} is not an object: {path}")
        try:
            entry_id = item["id"]
            summary = item["summary"]
        except KeyError as e:
            raise RiskRegisterError(
                f"risk register entry {i} missing required field: {e.args[0]!r}",
            ) from e
        refs = item.get("framework_refs", [])
        if not isinstance(refs, list):
            raise RiskRegisterError(
                f"risk register entry {entry_id!r}: framework_refs must be a list",
            )
        if entry_id in parsed:
            raise RiskRegisterError(f"risk register entry {entry_id!r} duplicated")

        # Parse optional threshold field.
        threshold = None
        threshold_raw = item.get("threshold")
        if threshold_raw is not None:
            if not isinstance(threshold_raw, dict):
                raise RiskRegisterError(
                    f"risk register entry {entry_id!r}: threshold must be an object",
                )
            try:
                threshold = Threshold(
                    framework=str(threshold_raw["framework"]),
                    magnitude_band_min=threshold_raw.get("magnitude_band_min"),
                    impact_tier_min=threshold_raw.get("impact_tier_min"),
                    risk_class_min=threshold_raw.get("risk_class_min"),
                    impact_min=threshold_raw.get("impact_min"),
                    severity_min=threshold_raw.get("severity_min"),
                )
            except KeyError as e:
                raise RiskRegisterError(
                    f"risk register entry {entry_id!r} threshold missing required field: {e.args[0]!r}",  # noqa: E501
                ) from e

        parsed[entry_id] = RiskRegisterEntry(
            id=str(entry_id),
            summary=str(summary),
            framework_refs=tuple(str(r) for r in refs),
            threshold=threshold,
        )
    return RiskRegister(entries=parsed)
