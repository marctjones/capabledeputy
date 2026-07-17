"""#379 — the one precedence lattice: most-restrictive composition + the
posture-vs-purpose risk-preference resolver (purpose may only tighten)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from capabledeputy.policy.envelope import RiskPreference
from capabledeputy.policy.precedence import (
    PrecedenceLevel,
    is_at_least_as_restrictive,
    most_restrictive,
    resolve_risk_preference,
    stricter_dial,
)
from capabledeputy.policy.rules import Decision

_DECISIONS = list(Decision)
_DIALS = list(RiskPreference)

# The canonical strict order, most-restrictive first.
_ORDER = [
    Decision.DENY,
    Decision.OVERRIDE_REQUIRED,
    Decision.REQUIRE_APPROVAL,
    Decision.WARN,
    Decision.ALLOW,
]


def test_precedence_levels_are_ordered_floor_highest() -> None:
    assert PrecedenceLevel.PURPOSE < PrecedenceLevel.POSTURE < PrecedenceLevel.RULE
    assert PrecedenceLevel.RULE < PrecedenceLevel.REQUIREMENT < PrecedenceLevel.FLOOR


def test_most_restrictive_matches_canonical_order() -> None:
    # DENY beats everything; ALLOW loses to everything.
    for i, stricter in enumerate(_ORDER):
        for looser in _ORDER[i:]:
            assert most_restrictive(stricter, looser) == stricter
            assert most_restrictive(looser, stricter) == stricter


def test_most_restrictive_empty_fails_closed() -> None:
    with pytest.raises(ValueError, match="at least one"):
        most_restrictive()


@given(ds=st.lists(st.sampled_from(_DECISIONS), min_size=1, max_size=5))
def test_most_restrictive_is_the_min_rank(ds: list[Decision]) -> None:
    result = most_restrictive(*ds)
    # No input is stricter than the result.
    assert all(is_at_least_as_restrictive(result, d) for d in ds)
    assert result in ds


# --- posture-vs-purpose dial resolution -----------------------------------


def test_purpose_may_only_tighten_never_loosen() -> None:
    # A permissive posture + a cautious purpose -> cautious (tightened).
    assert (
        resolve_risk_preference(RiskPreference.PERMISSIVE, RiskPreference.CAUTIOUS)
        == RiskPreference.CAUTIOUS
    )
    # A cautious posture + a permissive purpose -> cautious (purpose can't loosen).
    assert (
        resolve_risk_preference(RiskPreference.CAUTIOUS, RiskPreference.PERMISSIVE)
        == RiskPreference.CAUTIOUS
    )
    # No purpose override -> posture baseline unchanged.
    assert resolve_risk_preference(RiskPreference.BALANCED, None) == RiskPreference.BALANCED


@given(base=st.sampled_from(_DIALS), override=st.sampled_from([*_DIALS, None]))
def test_resolved_dial_never_more_autonomous_than_base(
    base: RiskPreference,
    override: RiskPreference | None,
) -> None:
    """The resolved dial is never MORE autonomous than the posture baseline —
    a purpose can only ratchet toward caution."""
    autonomy = {
        RiskPreference.CAUTIOUS: 0,
        RiskPreference.BALANCED: 1,
        RiskPreference.PERMISSIVE: 2,
    }
    resolved = resolve_risk_preference(base, override)
    assert autonomy[resolved] <= autonomy[base]
    # And it never tightens PAST the purpose's request either (it's exactly the
    # stricter of the two, or the base when no override).
    if override is not None:
        assert resolved == stricter_dial(base, override)


# --- wiring: a purpose can't loosen the posture at session spawn -----------


def test_spawn_dial_purpose_may_not_loosen_posture() -> None:
    """The SessionGraph resolves a spawned session's dial from the posture
    baseline + the purpose dial; a permissive purpose under a strict (cautious)
    posture spawns cautious, not permissive — the concrete #307/#379 gap."""
    from capabledeputy.session.graph import _resolve_spawn_dial

    # strict posture baseline + permissive purpose -> cautious (tightened).
    assert _resolve_spawn_dial(RiskPreference.CAUTIOUS, "permissive") == "cautious"
    # permissive posture + cautious purpose -> cautious (purpose tightens).
    assert _resolve_spawn_dial(RiskPreference.PERMISSIVE, "cautious") == "cautious"
    # no posture (legacy) -> purpose dial used directly.
    assert _resolve_spawn_dial(None, "permissive") == "permissive"
