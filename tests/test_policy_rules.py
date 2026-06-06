from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import (
    FINANCIAL_EMAIL_RULE,
    FINANCIAL_PURCHASE_RULE,
    HEALTH_EGRESS_RULE,
    PROVENANCE_EGRESS_RULE,
    _conflict_invariant_outcome,
)
from capabledeputy.policy.labels import (
    AxisA,
    AxisB,
    CategoryTag,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier


def test_conflict_invariant_untrusted_email_egress() -> None:
    """Axis-B external-untrusted + email egress → DENY."""
    axis_b = AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),))
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com")
    outcome = _conflict_invariant_outcome(axis_a=None, axis_b=axis_b, action=action)
    assert outcome is not None
    decision, rule, _ = outcome
    assert decision == Decision.DENY
    assert rule == PROVENANCE_EGRESS_RULE


def test_conflict_invariant_health_email_egress() -> None:
    """Axis-A health category + email egress → DENY."""
    axis_a = AxisA(categories=(CategoryTag(category="health", tier=Tier.REGULATED),))
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com")
    outcome = _conflict_invariant_outcome(axis_a=axis_a, axis_b=None, action=action)
    assert outcome is not None
    decision, rule, _ = outcome
    assert decision == Decision.DENY
    assert rule == HEALTH_EGRESS_RULE


def test_conflict_invariant_financial_email_egress() -> None:
    """Axis-A financial category + email egress → DENY."""
    axis_a = AxisA(categories=(CategoryTag(category="financial", tier=Tier.REGULATED),))
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com")
    outcome = _conflict_invariant_outcome(axis_a=axis_a, axis_b=None, action=action)
    assert outcome is not None
    decision, rule, _ = outcome
    assert decision == Decision.DENY
    assert rule == FINANCIAL_EMAIL_RULE


def test_conflict_invariant_financial_purchase_egress() -> None:
    """Axis-A financial category + purchase egress → REQUIRE_APPROVAL."""
    axis_a = AxisA(categories=(CategoryTag(category="financial", tier=Tier.REGULATED),))
    action = Action(kind=CapabilityKind.QUEUE_PURCHASE, target="amazon", amount=100)
    outcome = _conflict_invariant_outcome(axis_a=axis_a, axis_b=None, action=action)
    assert outcome is not None
    decision, rule, _ = outcome
    assert decision == Decision.REQUIRE_APPROVAL
    assert rule == FINANCIAL_PURCHASE_RULE


def test_conflict_invariant_no_egress_action() -> None:
    """Non-egress actions return None."""
    axis_a = AxisA(categories=(CategoryTag(category="health", tier=Tier.REGULATED),))
    action = Action(kind=CapabilityKind.READ_FS, target="/file")
    outcome = _conflict_invariant_outcome(axis_a=axis_a, axis_b=None, action=action)
    assert outcome is None


def test_conflict_invariant_personal_category_allows() -> None:
    """Personal category + egress is allowed (only health/financial conflict)."""
    axis_a = AxisA(categories=(CategoryTag(category="personal", tier=Tier.REGULATED),))
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com")
    outcome = _conflict_invariant_outcome(axis_a=axis_a, axis_b=None, action=action)
    assert outcome is None
