"""T089 — Control-plane reflexivity (FR-018 / SC-005).

A session that carries any `external-untrusted` provenance in
Axis-B cannot exercise an ADMINISTER-class effect — label/capability/
profile/audit/rule/binding/override-policy edits. The "tainted
session cannot edit the policy oracle that gates it" invariant.

This is the structural defense against a prompt-injected agent
quietly upgrading its own clearance.
"""

from __future__ import annotations

from capabledeputy.policy.assurance import (
    ControlPlaneEffect,
    control_plane_admissible,
    is_control_plane_effect,
)
from capabledeputy.policy.labels import (
    AxisB,
    AxisBEntry,
    ProvenanceLevel,
)


def test_administer_effects_are_recognized() -> None:
    for effect in ControlPlaneEffect:
        assert is_control_plane_effect(effect.value)


def test_data_plane_effect_is_not_control_plane() -> None:
    assert not is_control_plane_effect("data.read_file")
    assert not is_control_plane_effect("social.send_email")


def test_data_plane_always_admissible() -> None:
    """control_plane_admissible returns True for non-control-plane
    effects regardless of provenance — other gates govern those."""
    tainted = AxisB(
        entries=(AxisBEntry(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),),
    )
    assert control_plane_admissible(effect_class="data.read_file", axis_b=tainted)


def test_clean_session_admissible_for_control_plane() -> None:
    clean = AxisB(
        entries=(AxisBEntry(level=ProvenanceLevel.PRINCIPAL_DIRECT),),
    )
    for effect in ControlPlaneEffect:
        assert control_plane_admissible(effect_class=effect.value, axis_b=clean)


def test_tainted_session_refused_for_label_edit() -> None:
    """SC-005 — a session with external-untrusted provenance cannot
    touch label declarations."""
    tainted = AxisB(
        entries=(
            AxisBEntry(level=ProvenanceLevel.PRINCIPAL_DIRECT),
            AxisBEntry(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),
        ),
    )
    assert not control_plane_admissible(
        effect_class=ControlPlaneEffect.LABEL_EDIT.value,
        axis_b=tainted,
    )


def test_tainted_session_refused_for_every_administer_effect() -> None:
    tainted = AxisB(
        entries=(AxisBEntry(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),),
    )
    for effect in ControlPlaneEffect:
        assert not control_plane_admissible(
            effect_class=effect.value,
            axis_b=tainted,
        )


def test_empty_axis_b_treated_as_clean() -> None:
    """A session with no AxisB entries hasn't ingested any tainted
    input — treat as clean. The bind step raises taint; absence is
    the unraised default."""
    empty = AxisB(entries=())
    assert control_plane_admissible(
        effect_class=ControlPlaneEffect.RULE_EDIT.value,
        axis_b=empty,
    )


def test_system_internal_does_not_taint() -> None:
    """Only EXTERNAL_UNTRUSTED triggers the gate. System-internal
    inputs are above the taint threshold (FR-004 lattice)."""
    system_internal = AxisB(
        entries=(AxisBEntry(level=ProvenanceLevel.SYSTEM_INTERNAL),),
    )
    assert control_plane_admissible(
        effect_class=ControlPlaneEffect.LABEL_EDIT.value,
        axis_b=system_internal,
    )
