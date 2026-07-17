"""#388 — layered defaults: a fresh install runs from safe built-in defaults
(the shipped `strict` posture); operators override only deltas. Plus a
cross-phase integration test tying Phase 1 (precedence) + Phase 2 (compiler) +
Phase 3 (check/explain) together."""

from __future__ import annotations

from pathlib import Path

from capabledeputy.policy.authoring import (
    CompiledPolicy,
    apply_defaults,
    compile_document,
    load_config_with_defaults,
)
from capabledeputy.policy.envelope import RiskPreference
from capabledeputy.policy.posture import BUILTIN_POSTURES


def test_apply_defaults_fills_absent_posture_with_strict() -> None:
    filled = apply_defaults(CompiledPolicy())
    assert filled.posture is BUILTIN_POSTURES["strict"]


def test_apply_defaults_keeps_authored_posture() -> None:
    authored = compile_document({"posture": {"id": "mine", "dial": "permissive"}})
    filled = apply_defaults(authored)
    assert filled.posture is not None
    assert filled.posture.id == "mine"
    assert filled.posture.risk_preference == RiskPreference.PERMISSIVE


def test_load_with_defaults_missing_file_yields_strict(tmp_path: Path) -> None:
    compiled = load_config_with_defaults(tmp_path / "absent.yaml")
    assert compiled.posture is BUILTIN_POSTURES["strict"]
    assert compiled.rules.rules == ()


def test_load_with_defaults_none_path_yields_strict() -> None:
    assert load_config_with_defaults(None).posture is BUILTIN_POSTURES["strict"]


def test_load_with_defaults_operator_overrides_and_adds(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    p.write_text(
        "posture:\n  id: site\n  dial: balanced\n"
        "rules:\n  - id: r\n    when: financial + send_email\n    then: deny\n",
        encoding="utf-8",
    )
    compiled = load_config_with_defaults(p)
    assert compiled.posture is not None and compiled.posture.id == "site"
    assert compiled.posture.risk_preference == RiskPreference.BALANCED
    assert len(compiled.rules.rules) == 1


# --- cross-phase integration: author -> compile -> check -> explain --------


def test_end_to_end_unified_document_pipeline(tmp_path: Path) -> None:
    """One document exercises every phase: it compiles (Phase 2), validates
    clean (Phase 3 check), the strict-posture dial resolves cautiously (Phase 1),
    and an untrusted egress explains as a floor DENY (Phase 3 explain)."""
    from capabledeputy.policy.capabilities import CapabilityKind
    from capabledeputy.policy.explain import explain_decision
    from capabledeputy.policy.labels import LabelState, ProvenanceLevel, ProvenanceTag
    from capabledeputy.policy.policy_check import check_policy, has_errors
    from capabledeputy.policy.precedence import PrecedenceLevel, resolve_risk_preference
    from capabledeputy.policy.rules import Decision

    p = tmp_path / "capdep.yaml"
    p.write_text(
        "posture:\n"
        "  id: strict\n"
        "  dial: cautious\n"
        "  projection_only: true\n"
        "labels:\n"
        "  - category: financial\n"
        "    tier: restricted\n"
        "rules:\n"
        "  - id: no-external-financial\n"
        "    when: financial + send_email + external\n"
        "    then: deny\n"
        "    because: financial data may not be emailed externally\n",
        encoding="utf-8",
    )

    # Phase 2 — compiles cleanly, all sections present.
    compiled = load_config_with_defaults(p)
    assert compiled.posture is not None
    assert set(compiled.categories) == {"financial"}
    assert len(compiled.rules.rules) == 1

    # Phase 3 — cross-reference check is clean.
    assert not has_errors(check_policy(compiled))

    # Phase 1 — a permissive purpose can't loosen the strict posture's dial.
    resolved = resolve_risk_preference(
        compiled.posture.risk_preference,
        RiskPreference.PERMISSIVE,
    )
    assert resolved == RiskPreference.CAUTIOUS

    # Phase 3 — explain a floor decision.
    exp = explain_decision(
        labels=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
        kind=CapabilityKind.SEND_EMAIL,
        target="bob@example.com",
    )
    assert exp.decision == Decision.DENY
    assert exp.level == PrecedenceLevel.FLOOR
