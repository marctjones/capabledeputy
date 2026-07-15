"""#304 — security-posture manifest: a posture is data (a named binding over
existing dials), fail-closed on load, and may only ratchet stricter than the
structural floors."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.daily_driver import Retention
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.envelope import RiskPreference
from capabledeputy.policy.posture import (
    DEFAULT_FLOW_PATTERN_DEFAULTS,
    DEFAULT_POSTURE,
    Posture,
    PostureError,
    load_postures,
)
from capabledeputy.policy.tiers import Tier


def test_default_posture_validates_and_matches_todays_behavior() -> None:
    assert DEFAULT_POSTURE.projection_only is True
    assert DEFAULT_POSTURE.flow_pattern_for(Tier.NONE) == ExecutionMode.TURN_LEVEL
    assert DEFAULT_POSTURE.flow_pattern_for(Tier.RESTRICTED) == ExecutionMode.REFERENCE
    assert DEFAULT_POSTURE.flow_pattern_defaults == DEFAULT_FLOW_PATTERN_DEFAULTS


def test_validate_rejects_sub_floor_pattern() -> None:
    """A posture may not set a flow-pattern default weaker than the tier's
    structural floor — restricted requires Pattern 3/5 (FR-047)."""
    with pytest.raises(PostureError, match="weaker than the structural floor"):
        Posture(
            id="broken",
            flow_pattern_defaults={
                **DEFAULT_FLOW_PATTERN_DEFAULTS,
                Tier.RESTRICTED: ExecutionMode.TURN_LEVEL,  # below the REF/SEALED floor
            },
        ).validate()


def test_validate_allows_stricter_than_floor() -> None:
    """Ratcheting stricter is fine: regulated → REFERENCE is above its
    TURN_LEVEL floor."""
    p = Posture(
        id="strict",
        flow_pattern_defaults={
            **DEFAULT_FLOW_PATTERN_DEFAULTS,
            Tier.REGULATED: ExecutionMode.REFERENCE,
            Tier.SENSITIVE: ExecutionMode.DUAL_LLM,
        },
    ).validate()
    assert p.flow_pattern_for(Tier.REGULATED) == ExecutionMode.REFERENCE


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_load_postures_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PostureError, match="missing"):
        load_postures(tmp_path / "nope.yaml")


def test_load_postures_unparseable_fails_closed(tmp_path: Path) -> None:
    p = _write(tmp_path / "postures.yaml", "postures: [ : : bad")
    with pytest.raises(PostureError):
        load_postures(p)


def test_load_postures_bad_tier_or_pattern_fails_closed(tmp_path: Path) -> None:
    bad_pattern = _write(
        tmp_path / "a.yaml",
        "postures:\n  - id: x\n    flow_pattern_defaults:\n      regulated: not_a_mode\n",
    )
    with pytest.raises(PostureError, match="bad flow pattern"):
        load_postures(bad_pattern)

    bad_tier = _write(
        tmp_path / "b.yaml",
        "postures:\n  - id: x\n    flow_pattern_defaults:\n      nonsense_tier: turn_level\n",
    )
    with pytest.raises(PostureError, match="bad tier"):
        load_postures(bad_tier)


def test_load_postures_duplicate_id_fails_closed(tmp_path: Path) -> None:
    p = _write(tmp_path / "postures.yaml", "postures:\n  - id: dup\n  - id: dup\n")
    with pytest.raises(PostureError, match="duplicate id"):
        load_postures(p)


def test_load_postures_empty_yields_empty(tmp_path: Path) -> None:
    assert load_postures(_write(tmp_path / "postures.yaml", "postures: []\n")) == {}
    assert load_postures(_write(tmp_path / "e.yaml", "\n")) == {}


def test_load_postures_valid_roundtrip(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "postures.yaml",
        """
postures:
  - id: strict
    clearance_max_tier: restricted
    risk_preference: cautious
    projection_only: true
    retention: metadata
    inspector_set: [after_hours_purchase_tightener]
    flow_pattern_defaults:
      regulated: reference
      restricted: reference
  - id: low-friction
    risk_preference: permissive
    projection_only: false
    retention: artifact
    flow_pattern_defaults:
      regulated: turn_level
""",
    )
    postures = load_postures(p)
    assert set(postures) == {"strict", "low-friction"}

    strict = postures["strict"]
    assert strict.clearance_max_tier == Tier.RESTRICTED
    assert strict.risk_preference == RiskPreference.CAUTIOUS
    assert strict.retention == Retention.METADATA
    assert strict.inspector_set == ("after_hours_purchase_tightener",)
    assert strict.flow_pattern_for(Tier.REGULATED) == ExecutionMode.REFERENCE

    lf = postures["low-friction"]
    assert lf.risk_preference == RiskPreference.PERMISSIVE
    assert lf.projection_only is False
    assert lf.retention == Retention.ARTIFACT
    assert lf.flow_pattern_for(Tier.REGULATED) == ExecutionMode.TURN_LEVEL
