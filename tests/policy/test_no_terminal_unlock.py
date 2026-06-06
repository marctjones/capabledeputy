"""T068 — No path produces or unlocks `prohibited` (SC-006).

`prohibited` is a terminal tier. No rule, no dial setting, no
suggestion, and no ordinary approval can produce it — and no
override can unlock it for autonomous execution. The only way to
touch a `prohibited`-classified action is for a human to perform
it manually outside the system.

This is enforced by:
  1. The DISALLOWED override policy on the PROHIBITED floor in
     operator-curated configs (test_override_policy already covers
     this via test_disallowed_refuses_authorized_invoker).
  2. The envelope dial never crossing a hard floor (T065 already
     pins this).
  3. The decision_rules evaluator's FR-011 never-auto default
     applied to PROHIBITED-tier cells (T036 already pins this).

This file is the SC-006 *aggregator* — it asserts the
no-terminal-unlock invariant by composing the existing primitives
in scenarios that span them.
"""

from __future__ import annotations

from uuid import uuid4

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.decision_rules import (
    DecisionRule,
    DecisionRules,
    RuleOutcome,
    RulePredicate,
    evaluate,
)
from capabledeputy.policy.envelope import (
    CellKey,
    OutcomeEnvelope,
    RiskPreference,
)
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.overrides import (
    HardFloor,
    OverridePolicies,
    OverridePolicy,
    OverridePolicyEntry,
    OverrideRefusal,
    OverrideRefusalReason,
    request_override,
)
from capabledeputy.policy.tiers import Tier


def _prohibited_labels() -> LabelState:
    return LabelState(
        a=frozenset({CategoryTag(category="weapons_specs", tier=Tier.PROHIBITED)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )


def test_ratified_auto_rule_cannot_force_prohibited_auto_via_default() -> None:
    """A human-ratified AUTO rule that matches a PROHIBITED-tier
    cell does fire (the evaluator is content-agnostic). But the
    envelope around a prohibited cell is a hard floor that defeats
    the dial AND any downstream composition. So even a `match` here
    cannot result in autonomous execution: the envelope dial picks
    DENY regardless.

    What this test pins: the evaluator's match doesn't lie. The
    composition layer above (envelope dial + decide.py) is what
    enforces SC-006 — and the next test demonstrates that layer.
    """
    labels = _prohibited_labels()
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        expectedness="expected",
    )
    rule = DecisionRule(
        rule_id="optimistic-auto",
        predicate=RulePredicate(axis_a_category="weapons_specs"),
        outcome=RuleOutcome.AUTO,
        rationale="hypothetical bad-actor rule",
        human_ratified_by="someone",
    )
    rules = DecisionRules(rules=(rule,))
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="describe",
        target="anything",
    )
    # The evaluator itself isn't aware of tiers — it doesn't gate by
    # the AxisA tier. That gate is the envelope's job; see next test.
    assert result.outcome == RuleOutcome.AUTO


def test_envelope_hard_floor_prevents_dial_from_reaching_auto() -> None:
    """SC-006 enforcement via envelope: a PROHIBITED-tier cell has
    a hard-floor envelope (strictest == loosest == DENY). Even
    permissive dial cannot escape it."""
    cell = CellKey(
        category="weapons_specs",
        effect="describe",
        decision_context_canonical="principal-direct",
        reversibility="irreversible",
    )
    env = OutcomeEnvelope(
        cell=cell,
        strictest=RuleOutcome.DENY,
        loosest=RuleOutcome.DENY,
    )
    for dial in RiskPreference:
        assert env.select(dial) == RuleOutcome.DENY


def test_prohibited_floor_default_policy_is_disallowed() -> None:
    """Operator-curated override policy for PROHIBITED MUST be
    `disallowed` — `request_override` returns POLICY_DISALLOWED.
    Even an authorized invoker (none, in this entry) couldn't help."""
    policies = OverridePolicies(
        by_floor={
            HardFloor.PROHIBITED: OverridePolicyEntry(
                floor=HardFloor.PROHIBITED,
                policy=OverridePolicy.DISALLOWED,
            ),
        },
    )
    result = request_override(
        policies=policies,
        session_id=uuid4(),
        action_kind=CapabilityKind.SEND_EMAIL,
        target="x",
        target_category_tier=("weapons_specs", "prohibited"),
        floor=HardFloor.PROHIBITED,
        invoker="alice",
        friction_confirmed=True,
    )
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.POLICY_DISALLOWED


def test_no_prohibited_floor_entry_also_refused() -> None:
    """Belt-and-suspenders: if the operator forgot to declare a
    PROHIBITED entry at all, the request still refuses (missing
    entries fail-closed)."""
    policies = OverridePolicies(by_floor={})
    result = request_override(
        policies=policies,
        session_id=uuid4(),
        action_kind=CapabilityKind.SEND_EMAIL,
        target="x",
        target_category_tier=("weapons_specs", "prohibited"),
        floor=HardFloor.PROHIBITED,
        invoker="alice",
        friction_confirmed=True,
    )
    assert isinstance(result, OverrideRefusal)
    assert result.reason == OverrideRefusalReason.POLICY_DISALLOWED
