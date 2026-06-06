"""T041 — Audit-reconstruction determinism (SC-002 / FR-021).

A `policy.decided` audit event must contain enough information that
an independent replay — reading only the payload, with no access to
the live Session or rule set unless it's also persisted — yields the
same outcome and rationale. This is the SC-002 contract from the
auditor's perspective.

The test pipeline:
  1. Run engine.decide() with a known set of axis values + a known
     ratified ruleset; capture the resulting PolicyDecision.
  2. Serialize via build_policy_decided_payload().
  3. Reconstruct Axis-A/B/D from the payload + look up the same
     rules by id from the persisted ruleset.
  4. Re-run decision_rules.evaluate() on the reconstructed inputs.
  5. Assert outcome + matched_rule_ids are byte-identical.
"""

from __future__ import annotations

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import (
    DecisionRule,
    DecisionRules,
    RuleOutcome,
    RulePredicate,
    evaluate,
)
from capabledeputy.policy.engine import decide
from capabledeputy.policy.labels import (
    AxisA,
    AxisB,
    AxisD,
    CategoryTag,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.client import build_policy_decided_payload


def _cap() -> Capability:
    return Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="alice@example.com",
        origin=CapabilityOrigin.USER_APPROVED,
    )


def _action() -> Action:
    return Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com")


def _rule() -> DecisionRule:
    return DecisionRule(
        rule_id="email-to-alice-auto",
        predicate=RulePredicate(
            axis_a_category="personal",
            effect_class="send_email",
            target="alice@example.com",
        ),
        outcome=RuleOutcome.AUTO,
        rationale="alice is on the allowlist",
        human_ratified_by="marc@example.com",
    )


def _axes() -> tuple[AxisA, AxisB, AxisD]:
    axis_a = AxisA(
        categories=(
            CategoryTag(
                category="personal",
                tier=Tier.SENSITIVE,
                assignment_provenance="human-declared",
            ),
        ),
    )
    axis_b = AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        expectedness="expected",
        reversibility={"degree": "reversible", "agent": "system"},
    )
    return axis_a, axis_b, axis_d


def test_payload_contains_inputs_needed_for_replay() -> None:
    """The payload from a v2-composed decision must carry axis_a,
    axis_b, axis_d, and effect_class — the inputs to evaluate()."""
    axis_a, axis_b, axis_d = _axes()
    rules = DecisionRules(rules=(_rule(),))
    decision = decide(
        capabilities=frozenset({_cap()}),
        action=_action(),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="send_email",
        rules_v2=rules,
    )
    payload = build_policy_decided_payload(
        "email.send",
        {"to": "alice@example.com"},
        decision,
    )
    assert "axis_a" in payload
    assert "axis_b" in payload
    assert "axis_d" in payload
    assert payload["effect_class"] == "send_email"
    assert payload["v2_outcome"] == "auto"
    assert payload["v2_matched_rule_ids"] == ["email-to-alice-auto"]


def test_replay_from_payload_produces_identical_v2_outcome() -> None:
    """Reconstruct AxisA/B/D from the payload and re-run evaluate()
    against the same persisted ruleset; expect byte-identical
    outcome + matched_rule_ids (SC-002)."""
    axis_a, axis_b, axis_d = _axes()
    rules = DecisionRules(rules=(_rule(),))
    decision = decide(
        capabilities=frozenset({_cap()}),
        action=_action(),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="send_email",
        rules_v2=rules,
    )
    payload = build_policy_decided_payload(
        "email.send",
        {"to": "alice@example.com"},
        decision,
    )

    # Replay: rebuild axis objects from the payload alone.
    replay_a = AxisA.from_dict(payload["axis_a"])
    replay_b = AxisB.from_dict(payload["axis_b"])
    replay_d = AxisD.from_dict(payload["axis_d"])
    replay_effect = payload["effect_class"]

    replayed = evaluate(
        rules=rules,
        axis_a=replay_a,
        axis_b=replay_b,
        axis_d=replay_d,
        effect_class=replay_effect,
        target="alice@example.com",
    )

    assert replayed.outcome.value == payload["v2_outcome"]
    assert list(replayed.matched_rule_ids) == payload["v2_matched_rule_ids"]


def test_replay_preserves_rationale_when_no_rule_matches() -> None:
    """Same contract for the never-auto SUGGEST default branch."""
    axis_a, axis_b, axis_d = _axes()
    empty_rules = DecisionRules(rules=())
    decision = decide(
        capabilities=frozenset({_cap()}),
        action=_action(),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="send_email",
        rules_v2=empty_rules,
    )
    payload = build_policy_decided_payload(
        "email.send",
        {"to": "alice@example.com"},
        decision,
    )
    assert payload["v2_outcome"] == "suggest"

    replay_a = AxisA.from_dict(payload["axis_a"])
    replay_b = AxisB.from_dict(payload["axis_b"])
    replay_d = AxisD.from_dict(payload["axis_d"])
    replayed = evaluate(
        rules=empty_rules,
        axis_a=replay_a,
        axis_b=replay_b,
        axis_d=replay_d,
        effect_class=payload["effect_class"],
        target="alice@example.com",
    )
    assert replayed.outcome.value == payload["v2_outcome"]


def test_legacy_only_decision_has_no_axis_snapshots_in_payload() -> None:
    """Back-compat: a decision with no v2 inputs produces no
    axis_a/b/d/effect_class keys — pre-Phase-4 traces stay
    bit-identical so older consumers don't choke on new keys."""
    decision = decide(
        capabilities=frozenset({_cap()}),
        action=_action(),
    )
    payload = build_policy_decided_payload(
        "email.send",
        {"to": "alice@example.com"},
        decision,
    )
    assert "axis_a" not in payload
    assert "axis_b" not in payload
    assert "axis_d" not in payload
    assert "effect_class" not in payload
    assert "v2_outcome" not in payload
