"""T097 — Biba integrity floor refuses below-floor input (FR-004 / US5 scenario 2).

An integrity-floored step refuses inputs whose provenance level
falls below the floor. Direction: read-down refusal (Biba). The
lattice order is:

  principal-direct > system-internal > external-untrusted

A floor at `system-internal` accepts `system-internal` and
`principal-direct`; it refuses `external-untrusted`.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.resolution import (
    IntegrityFloorError,
    check_integrity_floor,
)


def test_principal_floor_accepts_principal() -> None:
    check_integrity_floor(
        floor_level="principal-direct",
        input_level="principal-direct",
    )


def test_principal_floor_refuses_system_internal() -> None:
    with pytest.raises(IntegrityFloorError):
        check_integrity_floor(
            floor_level="principal-direct",
            input_level="system-internal",
        )


def test_principal_floor_refuses_external_untrusted() -> None:
    with pytest.raises(IntegrityFloorError):
        check_integrity_floor(
            floor_level="principal-direct",
            input_level="external-untrusted",
        )


def test_system_internal_floor_accepts_principal() -> None:
    """Floor at system-internal accepts ABOVE-floor inputs too —
    Biba allows reading-UP (higher integrity is fine)."""
    check_integrity_floor(
        floor_level="system-internal",
        input_level="principal-direct",
    )


def test_system_internal_floor_accepts_system_internal() -> None:
    check_integrity_floor(
        floor_level="system-internal",
        input_level="system-internal",
    )


def test_system_internal_floor_refuses_external_untrusted() -> None:
    with pytest.raises(IntegrityFloorError):
        check_integrity_floor(
            floor_level="system-internal",
            input_level="external-untrusted",
        )


def test_external_untrusted_floor_is_degenerate() -> None:
    """A floor at external-untrusted demands nothing — everything
    passes. The operator can use this floor to make the integrity
    rule explicit but inert."""
    for input_level in ("principal-direct", "system-internal", "external-untrusted"):
        check_integrity_floor(
            floor_level="external-untrusted",
            input_level=input_level,
        )


def test_unknown_floor_level_fails_closed() -> None:
    """A typo in the operator config (or a malicious caller) — refuse
    rather than silently admit."""
    with pytest.raises(IntegrityFloorError):
        check_integrity_floor(floor_level="bogus", input_level="principal-direct")


def test_unknown_input_level_fails_closed() -> None:
    with pytest.raises(IntegrityFloorError):
        check_integrity_floor(floor_level="system-internal", input_level="bogus")
