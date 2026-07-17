"""#386 — explain_decision: the offline "why would this be denied?" renderer,
built on the real decide() and the precedence lattice."""

from __future__ import annotations

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.explain import explain_decision
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.precedence import PrecedenceLevel
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier


def test_untrusted_email_is_denied_at_the_floor() -> None:
    exp = explain_decision(
        labels=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
        kind=CapabilityKind.SEND_EMAIL,
        target="bob@example.com",
    )
    assert exp.decision == Decision.DENY
    assert exp.rule == "untrusted-meets-egress"
    assert exp.level == PrecedenceLevel.FLOOR
    assert "DENIED" in exp.summary()
    assert "floor" in exp.summary()


def test_health_email_is_denied_at_the_floor() -> None:
    exp = explain_decision(
        labels=LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)})),
        kind=CapabilityKind.SEND_EMAIL,
        target="doc@example.com",
    )
    assert exp.decision == Decision.DENY
    assert exp.level == PrecedenceLevel.FLOOR


def test_clean_read_is_allowed() -> None:
    exp = explain_decision(
        labels=LabelState(),
        kind=CapabilityKind.READ_FS,
        target="/home/op/notes.txt",
    )
    assert exp.decision == Decision.ALLOW
    assert "ALLOWED" in exp.summary()


def test_clearance_refusal_is_explained() -> None:
    exp = explain_decision(
        labels=LabelState(a=frozenset({CategoryTag("financial", Tier.RESTRICTED)})),
        kind=CapabilityKind.READ_FS,
        target="/x",
        clearance_max_tier=Tier.REGULATED,  # below restricted -> read-up refused
    )
    assert exp.decision == Decision.DENY
    assert exp.rule == "clearance-refused"
    assert exp.level == PrecedenceLevel.FLOOR


def test_summary_is_human_readable() -> None:
    exp = explain_decision(
        labels=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
        kind=CapabilityKind.SEND_EMAIL,
        target="bob@example.com",
    )
    s = exp.summary()
    assert s.startswith("This action")
    assert "untrusted-meets-egress" in s
