"""T065 — Risk-preference dial keeps every outcome within envelope (FR-030 / SC-010).

The owner's risk-preference profile selects a point inside each
cell's `{strictest, loosest}` envelope; hard-floor cells (degenerate
envelopes) are immovable. Crucial invariants:

  - Every dial value selects an outcome in [strictest, loosest].
  - Cautious dial never moves past `strictest` (the hard floor).
  - Permissive dial never moves past `loosest`.
  - Hard-floor cells return the floor regardless of the dial.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.decision_rules import RuleOutcome
from capabledeputy.policy.envelope import (
    CellKey,
    EnvelopeError,
    EnvelopeSet,
    OutcomeEnvelope,
    RiskPreference,
)


def _cell(label: str = "cell-1") -> CellKey:
    return CellKey(
        category="proprietary_work",
        effect="share",
        decision_context_canonical=label,
        reversibility="reversible",
    )


# --- envelope construction ------------------------------------------


def test_inverted_envelope_fails_construction() -> None:
    """strictest must be at least as restrictive as loosest. An
    inverted envelope is malformed — refuse at construction time."""
    with pytest.raises(EnvelopeError):
        OutcomeEnvelope(
            cell=_cell(),
            strictest=RuleOutcome.AUTO,
            loosest=RuleOutcome.DENY,
        )


def test_hard_floor_envelope_immovable() -> None:
    """strictest == loosest ⇒ degenerate envelope ⇒ every dial value
    returns the same outcome (SC-010)."""
    env = OutcomeEnvelope(
        cell=_cell(),
        strictest=RuleOutcome.DENY,
        loosest=RuleOutcome.DENY,
    )
    assert env.is_hard_floor
    assert env.select(RiskPreference.CAUTIOUS) == RuleOutcome.DENY
    assert env.select(RiskPreference.BALANCED) == RuleOutcome.DENY
    assert env.select(RiskPreference.PERMISSIVE) == RuleOutcome.DENY


# --- dial selection --------------------------------------------------


def test_cautious_picks_strictest() -> None:
    env = OutcomeEnvelope(
        cell=_cell(),
        strictest=RuleOutcome.REQUIRE_APPROVAL,
        loosest=RuleOutcome.AUTO,
    )
    assert env.select(RiskPreference.CAUTIOUS) == RuleOutcome.REQUIRE_APPROVAL


def test_permissive_picks_loosest() -> None:
    env = OutcomeEnvelope(
        cell=_cell(),
        strictest=RuleOutcome.REQUIRE_APPROVAL,
        loosest=RuleOutcome.AUTO,
    )
    assert env.select(RiskPreference.PERMISSIVE) == RuleOutcome.AUTO


def test_balanced_picks_middle_rounding_to_stricter() -> None:
    """Wide envelope: deny..auto. Balanced should pick the middle
    rank, rounded toward stricter ⇒ require-approval (rank 1, between
    deny=0 and auto=3, midpoint = 0 + (3-0)//2 = 1)."""
    env = OutcomeEnvelope(
        cell=_cell(),
        strictest=RuleOutcome.DENY,
        loosest=RuleOutcome.AUTO,
    )
    assert env.select(RiskPreference.BALANCED) == RuleOutcome.REQUIRE_APPROVAL


def test_dial_never_crosses_hard_floor() -> None:
    """A permissive dial cannot select an outcome below the
    cell's strictest. This is the SC-010 invariant under stress —
    even at the loosest end of the dial, the cell governs the floor."""
    env = OutcomeEnvelope(
        cell=_cell(),
        strictest=RuleOutcome.DENY,
        loosest=RuleOutcome.DENY,
    )
    assert env.select(RiskPreference.PERMISSIVE) == RuleOutcome.DENY


def test_dial_outcome_always_within_envelope() -> None:
    """Across a random-ish set of envelopes + dials, the selected
    outcome must be in [strictest, loosest] (rank-wise)."""
    rank: dict[RuleOutcome, int] = {
        RuleOutcome.DENY: 0,
        RuleOutcome.REQUIRE_APPROVAL: 1,
        RuleOutcome.SUGGEST: 2,
        RuleOutcome.AUTO: 3,
    }
    envelopes = [
        (RuleOutcome.DENY, RuleOutcome.DENY),
        (RuleOutcome.DENY, RuleOutcome.REQUIRE_APPROVAL),
        (RuleOutcome.DENY, RuleOutcome.SUGGEST),
        (RuleOutcome.DENY, RuleOutcome.AUTO),
        (RuleOutcome.REQUIRE_APPROVAL, RuleOutcome.REQUIRE_APPROVAL),
        (RuleOutcome.REQUIRE_APPROVAL, RuleOutcome.SUGGEST),
        (RuleOutcome.REQUIRE_APPROVAL, RuleOutcome.AUTO),
        (RuleOutcome.SUGGEST, RuleOutcome.SUGGEST),
        (RuleOutcome.SUGGEST, RuleOutcome.AUTO),
        (RuleOutcome.AUTO, RuleOutcome.AUTO),
    ]
    for s, hi in envelopes:
        env = OutcomeEnvelope(cell=_cell(f"{s}-{hi}"), strictest=s, loosest=hi)
        for dial in RiskPreference:
            outcome = env.select(dial)
            assert rank[s] <= rank[outcome] <= rank[hi], (
                f"envelope=[{s.value},{hi.value}] dial={dial.value} -> "
                f"{outcome.value} outside envelope"
            )


# --- EnvelopeSet lookup ---------------------------------------------


def test_lookup_returns_none_for_unknown_cell() -> None:
    envset = EnvelopeSet(by_cell={})
    assert envset.lookup(_cell()) is None


def test_lookup_returns_envelope_for_known_cell() -> None:
    cell = _cell()
    env = OutcomeEnvelope(
        cell=cell,
        strictest=RuleOutcome.SUGGEST,
        loosest=RuleOutcome.AUTO,
    )
    envset = EnvelopeSet(by_cell={cell: env})
    assert envset.lookup(cell) == env
