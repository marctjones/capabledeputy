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


# --- #305 — the three shipped presets and posture resolution ------------


def test_builtin_presets_exist_and_validate() -> None:
    from capabledeputy.policy.posture import BUILTIN_POSTURES

    assert set(BUILTIN_POSTURES) == {
        "strict",
        "high-security-useful",
        "low-friction-practical",
    }
    for pid, posture in BUILTIN_POSTURES.items():
        assert posture.id == pid
        posture.validate()  # raises on any sub-floor default


def test_builtin_presets_differ_by_dial_never_by_floor() -> None:
    """The #305 core invariant (what #306 fuzzes): all three presets inherit
    identical structural floors — restricted stays REFERENCE/SEALED and
    prohibited stays SEALED in every preset; only NONE/SENSITIVE/REGULATED
    dials and the risk/inspector/retention dials differ."""
    from capabledeputy.policy.posture import BUILTIN_POSTURES

    for posture in BUILTIN_POSTURES.values():
        assert posture.flow_pattern_for(Tier.RESTRICTED) == ExecutionMode.REFERENCE
        assert posture.flow_pattern_for(Tier.PROHIBITED) == ExecutionMode.SEALED
        # #359 secure default in every SHIPPED preset — raw-allowed-with-taint
        # is only ever an explicit operator override via a custom posture.
        assert posture.projection_only is True


def test_builtin_preset_dials() -> None:
    from capabledeputy.policy.posture import BUILTIN_POSTURES

    strict = BUILTIN_POSTURES["strict"]
    assert strict.risk_preference == RiskPreference.CAUTIOUS
    assert strict.flow_pattern_for(Tier.SENSITIVE) == ExecutionMode.REFERENCE
    assert strict.flow_pattern_for(Tier.REGULATED) == ExecutionMode.REFERENCE
    assert strict.inspector_set == ("after_hours_purchase_tightener",)  # tighteners only
    assert strict.retention == Retention.METADATA

    hsu = BUILTIN_POSTURES["high-security-useful"]
    assert hsu.risk_preference == RiskPreference.BALANCED
    assert hsu.flow_pattern_for(Tier.SENSITIVE) == ExecutionMode.TURN_LEVEL
    assert hsu.flow_pattern_for(Tier.REGULATED) == ExecutionMode.DUAL_LLM
    assert hsu.inspector_set == (
        "self_egress_relaxer",
        "after_hours_purchase_tightener",
    )
    assert hsu.retention == Retention.REDACTED

    lfp = BUILTIN_POSTURES["low-friction-practical"]
    assert lfp.risk_preference == RiskPreference.PERMISSIVE
    assert lfp.flow_pattern_for(Tier.REGULATED) == ExecutionMode.TURN_LEVEL
    assert lfp.retention == Retention.ARTIFACT


def test_resolve_posture_builtin_custom_and_unknown() -> None:
    from capabledeputy.policy.posture import BUILTIN_POSTURES, resolve_posture

    assert resolve_posture("strict") is BUILTIN_POSTURES["strict"]

    custom = {"site-policy": Posture(id="site-policy").validate()}
    assert resolve_posture("site-policy", custom).id == "site-policy"

    with pytest.raises(PostureError, match="unknown posture"):
        resolve_posture("nope", custom)


def test_resolve_posture_rejects_builtin_shadowing() -> None:
    """A custom posture may not redefine a shipped preset id — `strict` must
    always mean the shipped strict (fail-closed, not silent override)."""
    from capabledeputy.policy.posture import resolve_posture

    shadow = {"strict": Posture(id="strict", risk_preference=RiskPreference.PERMISSIVE)}
    with pytest.raises(PostureError, match="shadow"):
        resolve_posture("strict", shadow)


# --- #305 — daemon-config posture selection wiring ----------------------


def _pc(**kwargs):
    from capabledeputy.policy.context import PolicyContext

    return PolicyContext(**kwargs)


def test_apply_posture_absent_key_is_noop() -> None:
    from capabledeputy.daemon.lifecycle import apply_posture_from_config

    pc = _pc()
    out, messages = apply_posture_from_config({}, pc)
    assert out is pc
    assert messages == []
    assert out.active_posture is None


def test_apply_posture_selects_preset_and_binds_dials() -> None:
    from capabledeputy.daemon.lifecycle import apply_posture_from_config
    from capabledeputy.substrate.decision_inspectors_builtin import (
        AfterHoursPurchaseTightener,
        SelfEgressRelaxer,
    )

    pc = _pc(
        risk_preference=RiskPreference.PERMISSIVE,  # posture binds over this
        decision_inspectors=(
            SelfEgressRelaxer(self_addresses=frozenset({"me@example.com"})),
            AfterHoursPurchaseTightener(),
        ),
    )
    out, messages = apply_posture_from_config({"posture": "strict"}, pc)

    assert out.active_posture is not None
    assert out.active_posture.id == "strict"
    assert out.risk_preference == RiskPreference.CAUTIOUS  # bound by the posture
    # strict = tighteners only: the configured relaxer is deactivated.
    assert [type(i).__name__ for i in out.decision_inspectors] == [
        "AfterHoursPurchaseTightener",
    ]
    assert any("'strict' active" in m for m in messages)


def test_apply_posture_unknown_id_fails_closed() -> None:
    from capabledeputy.daemon.lifecycle import apply_posture_from_config

    with pytest.raises(PostureError, match="unknown posture"):
        apply_posture_from_config({"posture": "nope"}, _pc())


def test_apply_posture_missing_builtin_inspector_warns_not_fails() -> None:
    """A preset naming a builtin the operator hasn't configured still starts
    (safe-default instance) but the operator is told to parameterize it."""
    from capabledeputy.daemon.lifecycle import apply_posture_from_config

    out, messages = apply_posture_from_config({"posture": "high-security-useful"}, pc := _pc())
    assert pc.decision_inspectors == ()
    assert {type(i).__name__ for i in out.decision_inspectors} == {
        "SelfEgressRelaxer",
        "AfterHoursPurchaseTightener",
    }
    assert sum("WARNING" in m for m in messages) == 2


def test_apply_posture_custom_from_postures_yaml(tmp_path: Path) -> None:
    """A custom posture in configs/postures.yaml is selectable by id, and a
    custom file shadowing a preset id refuses."""
    from capabledeputy.daemon.lifecycle import apply_posture_from_config

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "postures.yaml").write_text(
        "postures:\n  - id: site-policy\n    risk_preference: balanced\n",
        encoding="utf-8",
    )
    out, _ = apply_posture_from_config({"posture": "site-policy"}, _pc(), configs_dir=configs)
    assert out.active_posture.id == "site-policy"
    assert out.risk_preference == RiskPreference.BALANCED

    (configs / "postures.yaml").write_text(
        "postures:\n  - id: strict\n    risk_preference: permissive\n",
        encoding="utf-8",
    )
    with pytest.raises(PostureError, match="shadow"):
        apply_posture_from_config({"posture": "strict"}, _pc(), configs_dir=configs)
