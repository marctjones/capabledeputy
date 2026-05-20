"""T087 — Risk register citation validation (FR-015 / SC-001).

Two invariants:
  - Every label MUST cite at least one risk-register id; zero
    citations is an orphan.
  - Every cited id MUST exist in the operator-loaded register;
    citations of unknown ids are orphans.

Both are runtime-refused at decide time. The static CI lint
(scripts/lint_risk_register.py) catches the structural side
(register entries with no framework_refs).
"""

from __future__ import annotations

from capabledeputy.policy.assurance import validate_label_citation
from capabledeputy.policy.risk_register import (
    RiskRegister,
    RiskRegisterEntry,
)


def _register() -> RiskRegister:
    return RiskRegister(
        entries={
            "RISK-PII-001": RiskRegisterEntry(
                id="RISK-PII-001",
                summary="Unintended disclosure of personal data",
                framework_refs=("NIST-CSF-PR.DS-5", "ISO-27001-A.8.10"),
            ),
            "RISK-PROP-002": RiskRegisterEntry(
                id="RISK-PROP-002",
                summary="Proprietary content leakage",
                framework_refs=("ISO-27001-A.8.10",),
            ),
        },
    )


def test_known_id_validates_clean() -> None:
    orphans = validate_label_citation(
        risk_ids=("RISK-PII-001",),
        register=_register(),
    )
    assert orphans == ()


def test_unknown_id_returns_orphan() -> None:
    """Citing a risk-id that doesn't exist in the register ⇒ orphan."""
    orphans = validate_label_citation(
        risk_ids=("RISK-UNKNOWN-999",),
        register=_register(),
    )
    assert orphans == ("RISK-UNKNOWN-999",)


def test_zero_citations_is_orphan() -> None:
    """A label with no risk_ids at all is also an orphan (SC-001)."""
    orphans = validate_label_citation(risk_ids=(), register=_register())
    assert orphans == ("<no risk_ids cited>",)


def test_partial_known_returns_only_unknown() -> None:
    """A mix of known + unknown — only the unknowns are returned."""
    orphans = validate_label_citation(
        risk_ids=("RISK-PII-001", "RISK-UNKNOWN", "RISK-PROP-002"),
        register=_register(),
    )
    assert orphans == ("RISK-UNKNOWN",)


def test_register_audit_orphans_finds_empty_refs() -> None:
    """The other half of SC-001: register entries with no
    framework_refs are themselves orphans (the lint check)."""
    register = RiskRegister(
        entries={
            "OK": RiskRegisterEntry(id="OK", summary="ok", framework_refs=("REF-1",)),
            "ORPHAN": RiskRegisterEntry(id="ORPHAN", summary="missing refs"),
        },
    )
    assert register.audit_orphans() == ["ORPHAN"]
