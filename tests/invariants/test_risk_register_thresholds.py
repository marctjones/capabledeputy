"""T134 — Risk Register threshold schema linting (FR-016 / FR-028).

CI invariant: refuse to ship a risk_register.json whose entries cite a
quantification-required framework (FAIR, NIST-AI-RMF, EU-AI-Act, FIPS-199)
but omit the `threshold` field. Constitution Principle VI: fail-closed.

This is a static schema lint, not a functional test. It verifies that:
1. Every entry with a quantification-required framework_ref has a threshold.
2. Threshold shapes match their declared frameworks.
3. No orphan risks (SC-001 — covered elsewhere, but enforced here too).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.risk_register import load as load_register

_QUANTIFICATION_REQUIRED_FRAMEWORKS = frozenset(
    {
        "FAIR",
        "NIST-AI-RMF",
        "EU-AI-Act",
        "FIPS-199",
    }
)


@pytest.fixture
def risk_register_path() -> Path:
    """Path to the risk register config file."""
    return Path(__file__).parent.parent.parent / "configs" / "risk_register.json"


class TestRiskRegisterThresholdSchema:
    """Lint the risk_register.json schema for threshold completeness."""

    def test_register_loads_without_error(self, risk_register_path: Path) -> None:
        """The register must load successfully."""
        register = load_register(risk_register_path)
        assert len(register) > 0, "register must have at least one entry"

    def test_all_quantification_required_frameworks_have_thresholds(
        self, risk_register_path: Path
    ) -> None:
        """Every entry citing FAIR, NIST-AI-RMF, EU-AI-Act, or FIPS-199
        MUST declare a threshold field (FR-028 fail-closed)."""
        register = load_register(risk_register_path)
        failures = []

        for risk_id, entry in register.entries.items():
            # Check if any framework_ref is quantification-required.
            refs_set = set(entry.framework_refs)
            quantified_frameworks = refs_set & _QUANTIFICATION_REQUIRED_FRAMEWORKS
            if quantified_frameworks and entry.threshold is None:
                failures.append(
                    f"{risk_id}: cites quantification-required framework(s) "
                    f"{quantified_frameworks} but has no threshold field"
                )

        assert not failures, "threshold compliance failures:\n" + "\n".join(
            f"  - {f}" for f in failures
        )

    def test_threshold_framework_field_required(self, risk_register_path: Path) -> None:
        """Every threshold must have a 'framework' field."""
        register = load_register(risk_register_path)
        for risk_id, entry in register.entries.items():
            if entry.threshold is not None:
                assert entry.threshold.framework, (
                    f"{risk_id}: threshold must declare 'framework' field"
                )

    def test_threshold_has_matching_value_field(self, risk_register_path: Path) -> None:
        """Every threshold must have exactly one matching value field."""
        register = load_register(risk_register_path)
        for risk_id, entry in register.entries.items():
            if entry.threshold is None:
                continue

            framework = entry.threshold.framework
            has_magnitude_band = entry.threshold.magnitude_band_min is not None
            has_impact_tier = entry.threshold.impact_tier_min is not None
            has_risk_class = entry.threshold.risk_class_min is not None
            has_impact = entry.threshold.impact_min is not None
            has_severity = entry.threshold.severity_min is not None

            if framework == "FAIR":
                assert has_magnitude_band and not any(
                    [has_impact_tier, has_risk_class, has_impact, has_severity]
                ), f"{risk_id}: FAIR thresholds must have magnitude_band_min only"
            elif framework == "NIST-AI-RMF":
                assert has_impact_tier and not any(
                    [has_magnitude_band, has_risk_class, has_impact, has_severity]
                ), f"{risk_id}: NIST-AI-RMF thresholds must have impact_tier_min only"
            elif framework == "EU-AI-Act":
                assert has_risk_class and not any(
                    [has_magnitude_band, has_impact_tier, has_impact, has_severity]
                ), f"{risk_id}: EU-AI-Act thresholds must have risk_class_min only"
            elif framework == "FIPS-199":
                assert has_impact and not any(
                    [has_magnitude_band, has_impact_tier, has_risk_class, has_severity]
                ), f"{risk_id}: FIPS-199 thresholds must have impact_min only"
            elif framework == "OWASP-RiskRating":
                assert has_severity and not any(
                    [has_magnitude_band, has_impact_tier, has_risk_class, has_impact]
                ), f"{risk_id}: OWASP-RiskRating thresholds must have severity_min only"

    def test_threshold_values_are_valid(self, risk_register_path: Path) -> None:
        """Threshold values must be from the allowed set for their framework."""
        register = load_register(risk_register_path)

        valid_magnitude_bands = frozenset({"M1", "M2", "M3", "M4", "M5"})
        valid_impact_tiers = frozenset({"tier-1", "tier-2", "tier-3", "tier-4"})
        valid_risk_classes = frozenset({"minimal", "limited", "high", "prohibited"})
        valid_impacts = frozenset({"low", "moderate", "high"})
        valid_severities = frozenset({"low", "medium", "high", "critical"})

        for risk_id, entry in register.entries.items():
            if entry.threshold is None:
                continue

            framework = entry.threshold.framework
            if framework == "FAIR" and entry.threshold.magnitude_band_min:
                assert entry.threshold.magnitude_band_min in valid_magnitude_bands, (
                    f"{risk_id}: invalid FAIR magnitude_band_min "
                    f"{entry.threshold.magnitude_band_min!r}"
                )
            elif framework == "NIST-AI-RMF" and entry.threshold.impact_tier_min:
                assert entry.threshold.impact_tier_min in valid_impact_tiers, (
                    f"{risk_id}: invalid NIST-AI-RMF impact_tier_min "
                    f"{entry.threshold.impact_tier_min!r}"
                )
            elif framework == "EU-AI-Act" and entry.threshold.risk_class_min:
                assert entry.threshold.risk_class_min in valid_risk_classes, (
                    f"{risk_id}: invalid EU-AI-Act risk_class_min "
                    f"{entry.threshold.risk_class_min!r}"
                )
            elif framework == "FIPS-199" and entry.threshold.impact_min:
                assert entry.threshold.impact_min in valid_impacts, (
                    f"{risk_id}: invalid FIPS-199 impact_min {entry.threshold.impact_min!r}"
                )
            elif framework == "OWASP-RiskRating" and entry.threshold.severity_min:
                assert entry.threshold.severity_min in valid_severities, (
                    f"{risk_id}: invalid OWASP-RiskRating severity_min "
                    f"{entry.threshold.severity_min!r}"
                )

    def test_no_orphan_framework_refs(self, risk_register_path: Path) -> None:
        """SC-001: every entry must cite at least one external framework (SC-001)."""
        register = load_register(risk_register_path)
        orphans = register.audit_orphans()
        assert not orphans, f"SC-001 violation: orphan risk ids: {orphans}"
