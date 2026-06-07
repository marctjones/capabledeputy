"""T097 — Biba integrity floor refuses below-floor input (FR-004 / US5 scenario 2).

MIGRATED R4b.4: now tests meets_required_floor instead of check_integrity_floor.
The same semantics apply — a required floor refuses below-floor input.
"""

from __future__ import annotations

from capabledeputy.policy.labels import (
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
    meets_required_floor,
)


def test_principal_floor_accepts_principal() -> None:
    state = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)}))
    assert meets_required_floor(state, ProvenanceLevel.PRINCIPAL_DIRECT) is True


def test_principal_floor_refuses_system_internal() -> None:
    state = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.SYSTEM_INTERNAL)}))
    assert meets_required_floor(state, ProvenanceLevel.PRINCIPAL_DIRECT) is False


def test_principal_floor_refuses_external_untrusted() -> None:
    state = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    assert meets_required_floor(state, ProvenanceLevel.PRINCIPAL_DIRECT) is False


def test_system_internal_floor_accepts_principal() -> None:
    """Floor at system-internal accepts ABOVE-floor inputs too —
    Biba allows reading-UP (higher integrity is fine)."""
    state = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)}))
    assert meets_required_floor(state, ProvenanceLevel.SYSTEM_INTERNAL) is True


def test_system_internal_floor_accepts_system_internal() -> None:
    state = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.SYSTEM_INTERNAL)}))
    assert meets_required_floor(state, ProvenanceLevel.SYSTEM_INTERNAL) is True


def test_system_internal_floor_refuses_external_untrusted() -> None:
    state = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    assert meets_required_floor(state, ProvenanceLevel.SYSTEM_INTERNAL) is False


def test_external_untrusted_floor_is_degenerate() -> None:
    """A floor at external-untrusted demands nothing — everything
    passes. The operator can use this floor to make the integrity
    rule explicit but inert."""
    levels = (
        ProvenanceLevel.PRINCIPAL_DIRECT,
        ProvenanceLevel.SYSTEM_INTERNAL,
        ProvenanceLevel.EXTERNAL_UNTRUSTED,
    )
    for level in levels:
        state = LabelState(b=frozenset({ProvenanceTag(level)}))
        assert meets_required_floor(state, ProvenanceLevel.EXTERNAL_UNTRUSTED) is True


def test_no_floor_always_passes() -> None:
    """A None floor (no requirement) accepts everything."""
    levels = (
        ProvenanceLevel.PRINCIPAL_DIRECT,
        ProvenanceLevel.SYSTEM_INTERNAL,
        ProvenanceLevel.EXTERNAL_UNTRUSTED,
    )
    for level in levels:
        state = LabelState(b=frozenset({ProvenanceTag(level)}))
        assert meets_required_floor(state, None) is True
