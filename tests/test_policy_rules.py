from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import CONFLICT_RULES, ConflictRule, Decision


def test_rule_does_not_fire_on_empty_set() -> None:
    rule = ConflictRule(
        name="x",
        triggers=frozenset({Label.CONFIDENTIAL_HEALTH}),
        conflicts=frozenset({Label.EGRESS_EMAIL}),
        decision=Decision.DENY,
    )
    assert not rule.fires(frozenset())


def test_rule_does_not_fire_on_trigger_alone() -> None:
    rule = ConflictRule(
        name="x",
        triggers=frozenset({Label.CONFIDENTIAL_HEALTH}),
        conflicts=frozenset({Label.EGRESS_EMAIL}),
        decision=Decision.DENY,
    )
    assert not rule.fires(frozenset({Label.CONFIDENTIAL_HEALTH}))


def test_rule_does_not_fire_on_conflict_alone() -> None:
    rule = ConflictRule(
        name="x",
        triggers=frozenset({Label.CONFIDENTIAL_HEALTH}),
        conflicts=frozenset({Label.EGRESS_EMAIL}),
        decision=Decision.DENY,
    )
    assert not rule.fires(frozenset({Label.EGRESS_EMAIL}))


def test_rule_fires_when_both_present() -> None:
    rule = ConflictRule(
        name="x",
        triggers=frozenset({Label.CONFIDENTIAL_HEALTH}),
        conflicts=frozenset({Label.EGRESS_EMAIL}),
        decision=Decision.DENY,
    )
    assert rule.fires(
        frozenset({Label.CONFIDENTIAL_HEALTH, Label.EGRESS_EMAIL}),
    )


def test_rule_fires_with_any_of_multiple_triggers() -> None:
    rule = ConflictRule(
        name="x",
        triggers=frozenset({Label.UNTRUSTED_EXTERNAL, Label.UNTRUSTED_USER_INPUT}),
        conflicts=frozenset({Label.EGRESS_EMAIL}),
        decision=Decision.DENY,
    )
    assert rule.fires(
        frozenset({Label.UNTRUSTED_USER_INPUT, Label.EGRESS_EMAIL}),
    )


def test_canonical_rules_present() -> None:
    rule_names = {r.name for r in CONFLICT_RULES}
    assert rule_names == {
        "untrusted-meets-egress",
        "health-meets-egress",
        "financial-meets-email",
        "financial-meets-purchase",
    }


def test_only_financial_purchase_requires_approval() -> None:
    by_decision = {r.name: r.decision for r in CONFLICT_RULES}
    assert by_decision["financial-meets-purchase"] == Decision.REQUIRE_APPROVAL
    for name, dec in by_decision.items():
        if name != "financial-meets-purchase":
            assert dec == Decision.DENY
