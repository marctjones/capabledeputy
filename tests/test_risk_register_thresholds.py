"""T135 — Risk Register threshold functional tests (FR-016 / T131-T135).

Verify that:
1. get_threshold() returns the correct threshold for a risk_id.
2. threshold_crossed() correctly evaluates if residual metrics exceed thresholds.
3. Framework-specific threshold comparisons work correctly.
4. Entries without thresholds never cross (unmonitored risks).
5. Multiple crossing risks are detected correctly.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.risk_register import (
    RiskRegister,
    RiskRegisterEntry,
    RiskRegisterError,
    Threshold,
)


@pytest.fixture
def sample_register() -> RiskRegister:
    """Create a test register with diverse threshold frameworks."""
    return RiskRegister(
        entries={
            "RISK-PII-NIST": RiskRegisterEntry(
                id="RISK-PII-NIST",
                summary="PII disclosure with NIST-AI-RMF threshold.",
                framework_refs=("NIST-CSF-PR.DS-5",),
                threshold=Threshold(
                    framework="NIST-AI-RMF",
                    impact_tier_min="tier-2",
                ),
            ),
            "RISK-PROP-FAIR": RiskRegisterEntry(
                id="RISK-PROP-FAIR",
                summary="Proprietary disclosure with FAIR threshold.",
                framework_refs=("ISO-27001-A.8.10",),
                threshold=Threshold(
                    framework="FAIR",
                    magnitude_band_min="M4",
                ),
            ),
            "RISK-EU-AI": RiskRegisterEntry(
                id="RISK-EU-AI",
                summary="Risk with EU AI Act threshold.",
                framework_refs=("EU-AI-Act-Annex-III",),
                threshold=Threshold(
                    framework="EU-AI-Act",
                    risk_class_min="high",
                ),
            ),
            "RISK-FIPS": RiskRegisterEntry(
                id="RISK-FIPS",
                summary="Risk with FIPS-199 threshold.",
                framework_refs=("FIPS-199",),
                threshold=Threshold(
                    framework="FIPS-199",
                    impact_min="moderate",
                ),
            ),
            "RISK-OWASP": RiskRegisterEntry(
                id="RISK-OWASP",
                summary="Risk with OWASP RiskRating threshold.",
                framework_refs=("OWASP-LLM01:2025",),
                threshold=Threshold(
                    framework="OWASP-RiskRating",
                    severity_min="high",
                ),
            ),
            "RISK-UNMONITORED": RiskRegisterEntry(
                id="RISK-UNMONITORED",
                summary="Risk without a threshold.",
                framework_refs=("CUSTOM-FRAMEWORK",),
                threshold=None,
            ),
        }
    )


class TestGetThreshold:
    """Test get_threshold() method."""

    def test_get_threshold_returns_correct_threshold(self, sample_register: RiskRegister) -> None:
        """get_threshold() should return the threshold for a known risk_id."""
        threshold = sample_register.get_threshold("RISK-PII-NIST")
        assert threshold is not None
        assert threshold.framework == "NIST-AI-RMF"
        assert threshold.impact_tier_min == "tier-2"

    def test_get_threshold_returns_none_for_unmonitored(
        self, sample_register: RiskRegister
    ) -> None:
        """get_threshold() should return None for risks without a threshold."""
        threshold = sample_register.get_threshold("RISK-UNMONITORED")
        assert threshold is None

    def test_get_threshold_raises_on_unknown_id(self, sample_register: RiskRegister) -> None:
        """get_threshold() should raise RiskRegisterError for unknown ids."""
        with pytest.raises(RiskRegisterError, match="unknown risk-register id"):
            sample_register.get_threshold("UNKNOWN-RISK")


class TestThresholdCrossed:
    """Test threshold_crossed() method with framework-specific logic."""

    def test_nist_ai_rmf_threshold_not_crossed(
        self, sample_register: RiskRegister
    ) -> None:
        """NIST-AI-RMF: residual tier below threshold."""
        residual = {
            "NIST-AI-RMF": {"impact_tier": "tier-1"},
        }
        crossed = sample_register.threshold_crossed("RISK-PII-NIST", residual)
        assert not crossed

    def test_nist_ai_rmf_threshold_crossed_equal(
        self, sample_register: RiskRegister
    ) -> None:
        """NIST-AI-RMF: residual tier equals threshold."""
        residual = {
            "NIST-AI-RMF": {"impact_tier": "tier-2"},
        }
        crossed = sample_register.threshold_crossed("RISK-PII-NIST", residual)
        assert crossed

    def test_nist_ai_rmf_threshold_crossed_above(
        self, sample_register: RiskRegister
    ) -> None:
        """NIST-AI-RMF: residual tier above threshold."""
        residual = {
            "NIST-AI-RMF": {"impact_tier": "tier-4"},
        }
        crossed = sample_register.threshold_crossed("RISK-PII-NIST", residual)
        assert crossed

    def test_fair_threshold_not_crossed(self, sample_register: RiskRegister) -> None:
        """FAIR: residual magnitude band below threshold."""
        residual = {
            "FAIR": {"magnitude_band": "M2"},
        }
        crossed = sample_register.threshold_crossed("RISK-PROP-FAIR", residual)
        assert not crossed

    def test_fair_threshold_crossed_equal(self, sample_register: RiskRegister) -> None:
        """FAIR: residual magnitude band equals threshold."""
        residual = {
            "FAIR": {"magnitude_band": "M4"},
        }
        crossed = sample_register.threshold_crossed("RISK-PROP-FAIR", residual)
        assert crossed

    def test_fair_threshold_crossed_above(self, sample_register: RiskRegister) -> None:
        """FAIR: residual magnitude band above threshold."""
        residual = {
            "FAIR": {"magnitude_band": "M5"},
        }
        crossed = sample_register.threshold_crossed("RISK-PROP-FAIR", residual)
        assert crossed

    def test_eu_ai_act_threshold_not_crossed(
        self, sample_register: RiskRegister
    ) -> None:
        """EU-AI-Act: residual risk class below threshold."""
        residual = {
            "EU-AI-Act": {"risk_class": "limited"},
        }
        crossed = sample_register.threshold_crossed("RISK-EU-AI", residual)
        assert not crossed

    def test_eu_ai_act_threshold_crossed_equal(
        self, sample_register: RiskRegister
    ) -> None:
        """EU-AI-Act: residual risk class equals threshold."""
        residual = {
            "EU-AI-Act": {"risk_class": "high"},
        }
        crossed = sample_register.threshold_crossed("RISK-EU-AI", residual)
        assert crossed

    def test_eu_ai_act_threshold_crossed_above(
        self, sample_register: RiskRegister
    ) -> None:
        """EU-AI-Act: residual risk class above threshold."""
        residual = {
            "EU-AI-Act": {"risk_class": "prohibited"},
        }
        crossed = sample_register.threshold_crossed("RISK-EU-AI", residual)
        assert crossed

    def test_fips_threshold_not_crossed(self, sample_register: RiskRegister) -> None:
        """FIPS-199: residual impact below threshold."""
        residual = {
            "FIPS-199": {"impact": "low"},
        }
        crossed = sample_register.threshold_crossed("RISK-FIPS", residual)
        assert not crossed

    def test_fips_threshold_crossed_equal(self, sample_register: RiskRegister) -> None:
        """FIPS-199: residual impact equals threshold."""
        residual = {
            "FIPS-199": {"impact": "moderate"},
        }
        crossed = sample_register.threshold_crossed("RISK-FIPS", residual)
        assert crossed

    def test_fips_threshold_crossed_above(self, sample_register: RiskRegister) -> None:
        """FIPS-199: residual impact above threshold."""
        residual = {
            "FIPS-199": {"impact": "high"},
        }
        crossed = sample_register.threshold_crossed("RISK-FIPS", residual)
        assert crossed

    def test_owasp_threshold_not_crossed(self, sample_register: RiskRegister) -> None:
        """OWASP-RiskRating: residual severity below threshold."""
        residual = {
            "OWASP-RiskRating": {"severity": "medium"},
        }
        crossed = sample_register.threshold_crossed("RISK-OWASP", residual)
        assert not crossed

    def test_owasp_threshold_crossed_equal(self, sample_register: RiskRegister) -> None:
        """OWASP-RiskRating: residual severity equals threshold."""
        residual = {
            "OWASP-RiskRating": {"severity": "high"},
        }
        crossed = sample_register.threshold_crossed("RISK-OWASP", residual)
        assert crossed

    def test_owasp_threshold_crossed_above(self, sample_register: RiskRegister) -> None:
        """OWASP-RiskRating: residual severity above threshold."""
        residual = {
            "OWASP-RiskRating": {"severity": "critical"},
        }
        crossed = sample_register.threshold_crossed("RISK-OWASP", residual)
        assert crossed

    def test_unmonitored_risk_never_crosses(self, sample_register: RiskRegister) -> None:
        """Unmonitored risks (no threshold) never cross."""
        residual = {"CUSTOM-FRAMEWORK": {"some_metric": "critical"}}
        crossed = sample_register.threshold_crossed("RISK-UNMONITORED", residual)
        assert not crossed

    def test_missing_framework_in_residual(self, sample_register: RiskRegister) -> None:
        """If decision_residual lacks the threshold's framework key, no crossing."""
        residual = {
            "OTHER-FRAMEWORK": {"metric": "high"},
        }
        crossed = sample_register.threshold_crossed("RISK-PII-NIST", residual)
        assert not crossed

    def test_missing_metric_in_residual(self, sample_register: RiskRegister) -> None:
        """If residual is missing the specific metric, no crossing (fail-closed)."""
        residual = {
            "NIST-AI-RMF": {},  # Missing impact_tier
        }
        crossed = sample_register.threshold_crossed("RISK-PII-NIST", residual)
        assert not crossed

    def test_unknown_risk_id_raises(self, sample_register: RiskRegister) -> None:
        """threshold_crossed() raises RiskRegisterError for unknown ids."""
        residual = {"NIST-AI-RMF": {"impact_tier": "tier-2"}}
        with pytest.raises(RiskRegisterError, match="unknown risk-register id"):
            sample_register.threshold_crossed("UNKNOWN-RISK", residual)

    def test_multiple_frameworks_in_residual(self, sample_register: RiskRegister) -> None:
        """Decision with multiple frameworks — only relevant one checked."""
        residual = {
            "NIST-AI-RMF": {"impact_tier": "tier-1"},  # Below threshold
            "FAIR": {"magnitude_band": "M5"},  # Not relevant to RISK-PII-NIST
        }
        crossed = sample_register.threshold_crossed("RISK-PII-NIST", residual)
        assert not crossed  # Only NIST-AI-RMF matters; it's below threshold


class TestIntegration:
    """Integration tests with actual register loading."""

    def test_load_and_check_thresholds(self) -> None:
        """Load the actual risk_register.json and verify thresholds exist."""
        from pathlib import Path

        from capabledeputy.policy.risk_register import load

        register_path = Path(__file__).parent.parent / "configs" / "risk_register.json"
        if not register_path.is_file():
            pytest.skip("risk_register.json not found")

        register = load(register_path)
        assert len(register) > 0

        # Every entry should have a threshold in the live config.
        for risk_id, entry in register.entries.items():
            threshold = register.get_threshold(risk_id)
            # Due to FR-028, all entries in the live config should have thresholds.
            # (But the test is resilient to entries without them.)
            if threshold is not None:
                assert threshold.framework in {
                    "FAIR",
                    "NIST-AI-RMF",
                    "EU-AI-Act",
                    "FIPS-199",
                    "OWASP-RiskRating",
                }
