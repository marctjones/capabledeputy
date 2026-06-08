"""Slice C — standing rules may cross structural floors over operator data.

Under the `personal` trust profile, a human-ratified rule that EXPLICITLY
names a structural conflict floor (`crosses_floor`) may auto-cross it — for
the operator's OWN data (health / financial). The sharp line the design
draws (operator autonomy ≠ adversary autonomy):

  - `untrusted-meets-egress` can NEVER be rule-crossed — not at load (the id
    is refused) and not at compose (a defense-in-depth guard). A standing
    rule can never auto-egress untrusted content.
  - `managed` never suppresses a floor — the rule's relaxation is re-floored.
  - Only human-RATIFIED rules participate (FR-014); an unratified rule (the
    only kind the model could ever influence) has zero effect.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import (
    DecisionRule,
    DecisionRuleError,
    DecisionRules,
    RuleOutcome,
    RulePredicate,
    load,
)
from capabledeputy.policy.engine import decide
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier

_HEALTH = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))
_UNTRUSTED = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
_AXIS_D = AxisD(initiator="principal:owner")
_EMAIL_CAP = frozenset(
    {
        Capability(
            kind=CapabilityKind.SEND_EMAIL,
            pattern="*",
            origin=CapabilityOrigin.USER_APPROVED,
        ),
    },
)


def _rule(
    *,
    crosses_floor: str | None,
    ratified: bool = True,
    provenance: str | None = None,
) -> DecisionRule:
    return DecisionRule(
        rule_id="r",
        predicate=RulePredicate(
            axis_a_category=None if provenance else "health",
            axis_b_provenance=provenance,
            effect_class="social.send_email",
        ),
        outcome=RuleOutcome.AUTO,
        rationale="operator-authored",
        human_ratified_by="owner" if ratified else None,
        crosses_floor=crosses_floor,
    )


def _decide(rule: DecisionRule, *, labels: LabelState, personal: bool):
    return decide(
        _EMAIL_CAP,
        Action(kind=CapabilityKind.SEND_EMAIL, target="doctor@example.com"),
        labels=labels,
        axis_d=_AXIS_D,
        effect_class="social.send_email",
        rules_v2=DecisionRules(rules=(rule,)),
        trust_profile_is_personal=personal,
    )


# --- personal: rule crosses a floor over the operator's own data ----


def test_personal_rule_crosses_health_egress() -> None:
    """A human-ratified rule naming `crosses_floor: health-meets-egress`
    auto-crosses the floor under `personal` — sending the operator's OWN
    health data proceeds without the conflict DENY."""
    result = _decide(
        _rule(crosses_floor="health-meets-egress"),
        labels=_HEALTH,
        personal=True,
    )
    assert result.decision == Decision.ALLOW


def test_managed_rule_does_not_cross() -> None:
    """The SAME rule under `managed` does NOT cross — the conflict floor
    re-applies (managed is unchanged; the power is gated by the profile)."""
    result = _decide(
        _rule(crosses_floor="health-meets-egress"),
        labels=_HEALTH,
        personal=False,
    )
    assert result.decision == Decision.DENY
    assert result.rule == "health-meets-egress"


def test_personal_rule_without_crosses_floor_is_still_floored() -> None:
    """Crossing must be EXPLICIT — a relaxing rule that does not name the
    floor cannot cross it by accident, even in `personal`."""
    result = _decide(
        _rule(crosses_floor=None),
        labels=_HEALTH,
        personal=True,
    )
    assert result.decision == Decision.DENY
    assert result.rule == "health-meets-egress"


# --- the hard line: untrusted-egress is never rule-crossable --------


def test_untrusted_floor_not_crossable_even_if_named_at_compose() -> None:
    """Defense in depth: even if a rule's crosses_floor somehow holds
    `untrusted-meets-egress` (bypassing the load-time refusal by building
    the DecisionRule directly), the engine NEVER suppresses the untrusted
    floor. A standing rule can never auto-egress untrusted content."""
    rule = _rule(crosses_floor="untrusted-meets-egress", provenance="external-untrusted")
    result = _decide(rule, labels=_UNTRUSTED, personal=True)
    assert result.decision == Decision.DENY
    assert result.rule == "untrusted-meets-egress"


def test_unratified_crossing_rule_has_zero_effect() -> None:
    """FR-014 — an UNRATIFIED rule (the only kind the model could ever
    influence) does not fire, so its crosses_floor never takes effect."""
    result = _decide(
        _rule(crosses_floor="health-meets-egress", ratified=False),
        labels=_HEALTH,
        personal=True,
    )
    assert result.decision == Decision.DENY
    assert result.rule == "health-meets-egress"


# --- load-time validation (the 'by construction' exclusion) ---------


def _write_rule(tmp_path, crosses_floor: str):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - rule_id: r\n"
        "    outcome: auto\n"
        "    human_ratified_by: owner\n"
        f"    crosses_floor: {crosses_floor}\n"
        "    when:\n"
        "      axis_a:\n"
        "        category: health\n",
        encoding="utf-8",
    )
    return p


def test_load_refuses_untrusted_crosses_floor(tmp_path) -> None:
    """The dangerous case is not even expressible: a rule that tries to
    cross untrusted-meets-egress is refused at load (profile-independent)."""
    with pytest.raises(DecisionRuleError, match="untrusted"):
        load(_write_rule(tmp_path, "untrusted-meets-egress"))


def test_load_refuses_non_structural_crosses_floor(tmp_path) -> None:
    """A hard floor (FR-026d) or garbage is not rule-crossable — only an
    Override Grant crosses those."""
    with pytest.raises(DecisionRuleError, match="structural"):
        load(_write_rule(tmp_path, "prohibited"))


def test_load_accepts_health_crosses_floor(tmp_path) -> None:
    rules = load(_write_rule(tmp_path, "health-meets-egress"))
    assert rules.rules[0].crosses_floor == "health-meets-egress"
