"""Egress escalation policy (FR-019 amended).

Irreversible *communication* egress (sending a message) routes to human
APPROVAL by default — the operator confirms the specific send. This config
lets the operator escalate **super-sensitive** data to OVERRIDE_REQUIRED
(must pre-authorize via an override grant) by data category or sensitivity
tier. Absent file ⇒ approval for all communication egress.

(Purchases / transactional commitments are NOT affected — they keep the
stricter DENY→override default.)

Schema (`configs/egress_escalation.yaml`):

    require_override_for:
      tiers: [restricted]        # e.g. force override for the top tier
      categories: [proprietary_work]   # or specific categories
"""

from __future__ import annotations

from pathlib import Path


class EgressEscalationError(RuntimeError):
    """Malformed egress_escalation.yaml. Fail-closed."""


def load_egress_escalation(path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Return (override_categories, override_tiers). Absent ⇒ (∅, ∅)."""
    if not path.is_file():
        return frozenset(), frozenset()
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise EgressEscalationError(f"egress_escalation unparseable: {path} — {e}") from e
    if not isinstance(raw, dict):
        raise EgressEscalationError("egress_escalation root must be a mapping")
    req = raw.get("require_override_for") or {}
    if not isinstance(req, dict):
        raise EgressEscalationError("require_override_for must be a mapping")
    categories = frozenset(str(c) for c in (req.get("categories") or []))
    tiers = frozenset(str(t) for t in (req.get("tiers") or []))
    return categories, tiers
