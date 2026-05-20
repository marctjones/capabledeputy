"""T088 — Residual-Risk Exception emission (FR-016 / SC-007).

Every threshold-crossing ALLOW produces exactly one
`residual_risk.exception` event. Non-ALLOW outcomes don't constitute
residual risk (the gate caught them). Per-decision dedup is the
caller's responsibility — this module surfaces the structured
signal.
"""

from __future__ import annotations

from capabledeputy.policy.assurance import (
    ResidualRiskThresholds,
    should_emit_residual_risk,
)


def _thresholds() -> ResidualRiskThresholds:
    return ResidualRiskThresholds(
        threshold_risk_ids=frozenset(
            {"RISK-PII-001", "RISK-PROP-002", "RISK-EGRESS-005"},
        ),
    )


def test_allow_with_threshold_id_emits() -> None:
    signal = should_emit_residual_risk(
        decision_is_allow=True,
        decision_risk_ids=("RISK-PII-001",),
        thresholds=_thresholds(),
    )
    assert signal.should_emit
    assert signal.crossed == ("RISK-PII-001",)


def test_deny_never_emits_even_at_threshold() -> None:
    """DENY caught it — no residual risk to report."""
    signal = should_emit_residual_risk(
        decision_is_allow=False,
        decision_risk_ids=("RISK-PII-001",),
        thresholds=_thresholds(),
    )
    assert not signal.should_emit
    assert signal.crossed == ()


def test_allow_without_threshold_id_does_not_emit() -> None:
    signal = should_emit_residual_risk(
        decision_is_allow=True,
        decision_risk_ids=("RISK-LOW-100",),
        thresholds=_thresholds(),
    )
    assert not signal.should_emit


def test_allow_partial_match_emits_only_for_crossed() -> None:
    """Multi-id decision: only the crossing ids show in the
    `crossed` field — auditors see which threshold fired."""
    signal = should_emit_residual_risk(
        decision_is_allow=True,
        decision_risk_ids=("RISK-LOW-100", "RISK-PII-001"),
        thresholds=_thresholds(),
    )
    assert signal.should_emit
    assert signal.crossed == ("RISK-PII-001",)


def test_empty_thresholds_never_emits() -> None:
    """If the operator declared no thresholds, no events fire —
    the operator opts in to the residual-risk reporting."""
    signal = should_emit_residual_risk(
        decision_is_allow=True,
        decision_risk_ids=("RISK-PII-001",),
        thresholds=ResidualRiskThresholds(),
    )
    assert not signal.should_emit


def test_decision_with_no_risk_ids_does_not_emit() -> None:
    signal = should_emit_residual_risk(
        decision_is_allow=True,
        decision_risk_ids=(),
        thresholds=_thresholds(),
    )
    assert not signal.should_emit
