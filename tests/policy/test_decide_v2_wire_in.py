"""T044 / T045 integration tests: engine.decide() wire-in of the v2
DecisionRule evaluator.

These tests cover the composition contract between the legacy
(v0.7) policy decision path and the v2 (003 US2) axis-based
evaluator:

- Back-compat: omitting v2 inputs ⇒ identical to legacy behavior.
- Asymmetry (FR-031): v2 may only ratchet stricter; v2 AUTO cannot
  override a legacy DENY.
- Never-auto default (FR-011): no matching v2 rule ⇒ v2 contributes
  SUGGEST (ratchets a legacy ALLOW to REQUIRE_APPROVAL).
- Human-ratified only (FR-014): an unratified rule does not fire,
  even if it would have matched.
- Audit fields populated: v2_outcome and v2_matched_rule_ids appear
  on the returned PolicyDecision whenever v2 ran (T048-adjacent).
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
)
from capabledeputy.policy.engine import V2_RULE_PREFIX, decide
from capabledeputy.policy.labels import (
    AxisA,
    AxisB,
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier


def _send_email_cap() -> Capability:
    """A capability matching SEND_EMAIL to alice@example.com."""
    return Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="alice@example.com",
        origin=CapabilityOrigin.USER_APPROVED,
    )


def _send_action() -> Action:
    return Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com")


def _axis_a_personal() -> AxisA:
    return AxisA(
        categories=(
            CategoryTag(
                category="personal",
                tier=Tier.SENSITIVE,
                assignment_provenance="human-declared",
            ),
        ),
    )


def _axis_b_direct() -> AxisB:
    return AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT),))


def _axis_d_principal() -> AxisD:
    return AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        expectedness="expected",
        reversibility={"degree": "reversible", "agent": "system"},
    )


def _ratified_auto_rule() -> DecisionRule:
    return DecisionRule(
        rule_id="email-to-alice-auto",
        predicate=RulePredicate(
            axis_a_category="personal",
            effect_class="send_email",
            target="alice@example.com",
        ),
        outcome=RuleOutcome.AUTO,
        rationale="alice is a known recipient; principal-initiated; reversible",
        human_ratified_by="marc@example.com",
    )


def _ratified_deny_rule() -> DecisionRule:
    return DecisionRule(
        rule_id="block-personal-email",
        predicate=RulePredicate(
            axis_a_category="personal",
            effect_class="send_email",
        ),
        outcome=RuleOutcome.DENY,
        rationale="ad-hoc kill switch for personal-data egress",
        human_ratified_by="marc@example.com",
    )


# --- back-compat ------------------------------------------------------


def test_omitting_v2_inputs_yields_legacy_behavior() -> None:
    """With no v2 axes or rule set, decide() returns exactly what the
    legacy engine would. v2 audit fields stay at their defaults."""
    cap = _send_email_cap()
    result = decide(
        frozenset({cap}),
        _send_action(),
        labels=LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)})),
    )
    assert result.decision == Decision.ALLOW
    assert result.v2_outcome is None
    assert result.v2_matched_rule_ids == ()


def test_partial_v2_inputs_yields_legacy_behavior() -> None:
    """If any v2 input is missing (here: rules_v2), v2 does not run."""
    cap = _send_email_cap()
    result = decide(
        frozenset({cap}),
        _send_action(),
        axis_a=_axis_a_personal(),
        axis_b=_axis_b_direct(),
        axis_d=_axis_d_principal(),
        effect_class="send_email",
        rules_v2=None,
    )
    assert result.decision == Decision.ALLOW
    assert result.v2_outcome is None


# --- v2 ratchets stricter --------------------------------------------


def test_v2_default_suggest_ratchets_legacy_allow_to_require_approval() -> None:
    """FR-011 never-auto default: when no human-ratified rule matches,
    v2 contributes SUGGEST → REQUIRE_APPROVAL on the legacy chokepoint."""
    cap = _send_email_cap()
    empty_rules = DecisionRules(rules=())
    result = decide(
        frozenset({cap}),
        _send_action(),
        axis_a=_axis_a_personal(),
        axis_b=_axis_b_direct(),
        axis_d=_axis_d_principal(),
        effect_class="send_email",
        rules_v2=empty_rules,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.v2_outcome == RuleOutcome.SUGGEST
    assert result.v2_matched_rule_ids == ()
    assert result.rule is not None and result.rule.startswith(V2_RULE_PREFIX)


def test_v2_deny_ratchets_legacy_allow_to_deny() -> None:
    rules_v2 = DecisionRules(rules=(_ratified_deny_rule(),))
    result = decide(
        frozenset({_send_email_cap()}),
        _send_action(),
        axis_a=_axis_a_personal(),
        axis_b=_axis_b_direct(),
        axis_d=_axis_d_principal(),
        effect_class="send_email",
        rules_v2=rules_v2,
    )
    assert result.decision == Decision.DENY
    assert result.v2_outcome == RuleOutcome.DENY
    assert result.v2_matched_rule_ids == ("block-personal-email",)


# --- v2 cannot relax (FR-031 asymmetry) ------------------------------


def test_v2_auto_cannot_override_legacy_deny() -> None:
    """Legacy untrusted-meets-egress fires DENY. Even a human-ratified
    AUTO rule cannot relax that. v2_outcome is still recorded for audit."""
    cap = _send_email_cap()
    rules_v2 = DecisionRules(rules=(_ratified_auto_rule(),))
    result = decide(
        frozenset({cap}),
        _send_action(),
        axis_a=_axis_a_personal(),
        axis_b=AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),)),
        axis_d=_axis_d_principal(),
        effect_class="send_email",
        rules_v2=rules_v2,
    )
    assert result.decision == Decision.DENY
    assert result.rule == "untrusted-meets-egress"  # legacy rule preserved
    assert result.v2_outcome == RuleOutcome.AUTO
    assert result.v2_matched_rule_ids == ("email-to-alice-auto",)


def test_v2_auto_preserves_legacy_allow() -> None:
    """When both paths green-light, decision stays ALLOW; v2 audit
    fields still populated."""
    rules_v2 = DecisionRules(rules=(_ratified_auto_rule(),))
    result = decide(
        frozenset({_send_email_cap()}),
        _send_action(),
        axis_a=_axis_a_personal(),
        axis_b=_axis_b_direct(),
        axis_d=_axis_d_principal(),
        effect_class="send_email",
        rules_v2=rules_v2,
    )
    assert result.decision == Decision.ALLOW
    assert result.v2_outcome == RuleOutcome.AUTO
    assert result.v2_matched_rule_ids == ("email-to-alice-auto",)


# --- FR-014 only ratified rules fire ---------------------------------


def test_unratified_auto_rule_does_not_fire_in_wire_in() -> None:
    """An unratified rule does not match — so v2 falls back to the
    never-auto default (SUGGEST) and the wire-in ratchets stricter."""
    unratified = DecisionRule(
        rule_id="alice-allow",
        predicate=RulePredicate(
            axis_a_category="personal",
            effect_class="send_email",
            target="alice@example.com",
        ),
        outcome=RuleOutcome.AUTO,
        rationale="not yet ratified",
        human_ratified_by=None,
    )
    rules_v2 = DecisionRules(rules=(unratified,))
    result = decide(
        frozenset({_send_email_cap()}),
        _send_action(),
        axis_a=_axis_a_personal(),
        axis_b=_axis_b_direct(),
        axis_d=_axis_d_principal(),
        effect_class="send_email",
        rules_v2=rules_v2,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.v2_outcome == RuleOutcome.SUGGEST
    assert result.v2_matched_rule_ids == ()


# --- default-deny knob -----------------------------------------------


def test_v2_default_deny_ratchets_legacy_allow_to_deny() -> None:
    """Operator may pass default_v2_outcome=DENY for stricter cells:
    no matching rule ⇒ DENY (still FR-011 compliant — never AUTO)."""
    result = decide(
        frozenset({_send_email_cap()}),
        _send_action(),
        axis_a=_axis_a_personal(),
        axis_b=_axis_b_direct(),
        axis_d=_axis_d_principal(),
        effect_class="send_email",
        rules_v2=DecisionRules(rules=()),
        default_v2_outcome=RuleOutcome.DENY,
    )
    assert result.decision == Decision.DENY
    assert result.v2_outcome == RuleOutcome.DENY
