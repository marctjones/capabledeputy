"""#306 surface D — decision-inspector composition: TIGHTEN beats RELAX,
end-to-end, and a relax can never cross a structural floor.

Decision inspectors (operator-supplied relax/tighten hooks, including Starlark
scripts) run AFTER `decide()` in `ToolPolicyHooks.apply_decision_inspectors`.
Two guarantees make operator-writable inspectors safe, both fuzzed here at the
pure-composition layer:

  D1  `compose_inspector_outcomes`: any TIGHTEN beats any RELAX, so a relaxer
      firing alongside a tightener can never win. (#306: "composition
      monotonicity holds — TIGHTEN beats RELAX — end-to-end.")
  D2  The chokepoint floor guard (`policy_hooks.py` ~line 166-186): a composed
      relax is applied ONLY when the base decision is exactly REQUIRE_APPROVAL.
      Against a DENY / OVERRIDE_REQUIRED base the relax is REFUSED. This test
      replicates that guard predicate over the pure composition output and
      asserts no relax survives against a floor base — the same predicate the
      chokepoint enforces, exercised over the full outcome space.

Beyond the TLA model (which has no inspector concept).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from capabledeputy.policy.rules import Decision
from capabledeputy.substrate.decision_inspector_port import (
    DecisionRelax,
    DecisionTighten,
    compose_inspector_outcomes,
    is_strictly_less_restrictive,
    is_strictly_more_restrictive,
)

_SETTINGS = settings(max_examples=400, deadline=None)

_DECISIONS = list(Decision)
_FLOOR_BASES = frozenset({Decision.DENY, Decision.OVERRIDE_REQUIRED})

_relax = st.builds(
    DecisionRelax,
    to=st.sampled_from(_DECISIONS),
    rule=st.just("relax"),
)
_tighten = st.builds(
    DecisionTighten,
    to=st.sampled_from(_DECISIONS),
    rule=st.just("tighten"),
)
_outcomes = st.lists(
    st.tuples(st.text(min_size=1, max_size=6), st.one_of(_relax, _tighten, st.none())),
    max_size=5,
)


@given(base=st.sampled_from(_DECISIONS), outcomes=_outcomes)
@_SETTINGS
def test_tighten_beats_relax(base: Decision, outcomes) -> None:
    """D1 — if any inspector proposes a TIGHTEN that would strictly tighten the
    base, the composed winner is never looser than the base."""
    composed = compose_inspector_outcomes(base, outcomes)
    if composed is None:
        return
    winner, _rule, _why = composed
    has_valid_tighten = any(
        isinstance(oc, DecisionTighten) and is_strictly_more_restrictive(oc.to, base)
        for _n, oc in outcomes
    )
    if has_valid_tighten:
        # A valid tighten exists ⇒ the winner must be a tighten (stricter than
        # base), never a relax.
        assert is_strictly_more_restrictive(winner, base), (
            f"tighten present but winner {winner} is not stricter than base {base}"
        )


@given(base=st.sampled_from(_DECISIONS), outcomes=_outcomes)
@_SETTINGS
def test_relax_never_crosses_a_floor_base(base: Decision, outcomes) -> None:
    """D2 — replicating the chokepoint floor guard: a composed relax is applied
    only when base == REQUIRE_APPROVAL. Against a DENY/OVERRIDE_REQUIRED base a
    relax is refused, so the effective decision stays the floor."""
    composed = compose_inspector_outcomes(base, outcomes)
    effective = base if composed is None else composed[0]

    # The exact predicate from policy_hooks.apply_decision_inspectors: a strictly
    # looser move is refused unless the base is REQUIRE_APPROVAL.
    if (
        composed is not None
        and is_strictly_less_restrictive(effective, base)
        and base != Decision.REQUIRE_APPROVAL
    ):
        effective = base  # refused by the guard

    if base in _FLOOR_BASES:
        # The floor is never LOOSENED. A tighten (e.g. OVERRIDE_REQUIRED → DENY)
        # is legitimate — the property is "no relax survives against a floor",
        # not "unchanged".
        assert not is_strictly_less_restrictive(effective, base), (
            f"floor base {base} was relaxed to {effective} — inspector crossed a floor"
        )


# --- Integration: the REAL chokepoint guard, not a copy of it -------------


class _AlwaysRelax:
    """An operator inspector that always tries to relax to ALLOW — the exact
    thing the floor guard must refuse against a DENY/OVERRIDE base."""

    name = "always-relax"

    def inspect(self, *, action, session, proposed_outcome):
        from capabledeputy.substrate.decision_inspector_port import DecisionRelax

        return DecisionRelax(to=Decision.ALLOW, rule="operator-ok", rationale="try to relax")


@pytest.mark.parametrize("base", [Decision.DENY, Decision.OVERRIDE_REQUIRED])
async def test_real_chokepoint_refuses_relax_against_floor(base: Decision, tmp_path) -> None:
    """D2 (integration) — drive the REAL `apply_decision_inspectors` (not a
    replica of its predicate) with a relaxing inspector against a DENY /
    OVERRIDE_REQUIRED base, and assert the floor survives and a
    RELAXATION_REFUSED event is emitted. Editing the guard in
    `policy_hooks.py` would fail THIS test."""
    from uuid import uuid4

    from capabledeputy.audit.events import EventType
    from capabledeputy.audit.writer import AuditWriter
    from capabledeputy.policy.actions import Action
    from capabledeputy.policy.capabilities import CapabilityKind
    from capabledeputy.policy.context import PolicyContext
    from capabledeputy.policy.engine import PolicyDecision
    from capabledeputy.session.graph import SessionGraph
    from capabledeputy.session.model import Session
    from capabledeputy.tools.policy_hooks import ToolPolicyHooks

    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(decision_inspectors=(_AlwaysRelax(),)),
        audit=audit,
        graph=graph,
    )
    adjusted = await hooks.apply_decision_inspectors(
        uuid4(),
        Session.new(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        "email.send",
        PolicyDecision(decision=base, rule="structural-floor"),
    )
    assert adjusted.decision == base, f"floor {base} was crossed by an inspector relax"
    events = await audit.read_all()
    assert any(e.event_type == EventType.RELAXATION_REFUSED for e in events)
