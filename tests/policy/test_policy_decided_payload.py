"""T048 — POLICY_DECIDED audit payload includes v2 fields when set.

The audit event's payload is the wire format used by T041 audit-
reconstruction: given the persisted payload, a replay must produce
the same outcome and rationale. These tests pin the payload shape
so that contract holds across both legacy-only and v2-composed
decisions.
"""

from __future__ import annotations

from capabledeputy.policy.decision_rules import RuleOutcome
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.rules import Decision
from capabledeputy.tools.client import build_policy_decided_payload


def test_payload_omits_v2_fields_when_v2_did_not_run() -> None:
    """A legacy-only decision omits v2 fields but still carries the
    replayable policy_trace envelope."""
    decision = PolicyDecision(
        decision=Decision.ALLOW,
        rule=None,
        reason=None,
    )
    payload = build_policy_decided_payload("fs.read", {"path": "/x"}, decision)
    assert "v2_outcome" not in payload
    assert "v2_matched_rule_ids" not in payload
    assert payload["decision"] == "allow"
    assert payload["policy_trace"]["tool"] == "fs.read"
    assert payload["policy_trace"]["decision"] == "allow"


def test_payload_includes_v2_fields_when_v2_ran() -> None:
    """When the v2 leg evaluated (regardless of whether it ratcheted),
    v2_outcome + v2_matched_rule_ids land in the payload — enough for
    T041 replay to reconstruct the composition."""
    decision = PolicyDecision(
        decision=Decision.REQUIRE_APPROVAL,
        rule="v2:default",
        reason="no human-ratified rule matched; default=suggest",
        v2_outcome=RuleOutcome.SUGGEST,
        v2_matched_rule_ids=(),
    )
    payload = build_policy_decided_payload("fs.read", {"path": "/x"}, decision)
    assert payload["v2_outcome"] == "suggest"
    assert payload["v2_matched_rule_ids"] == []
    assert payload["decision"] == "require_approval"
    assert payload["rule"] == "v2:default"
    assert payload["policy_trace"]["v2_outcome"] == "suggest"


def test_payload_includes_matched_rule_ids() -> None:
    """A rule-matched v2 outcome surfaces its rule ids in the payload."""
    decision = PolicyDecision(
        decision=Decision.DENY,
        rule="v2:block-personal-email",
        reason="matched rules=['block-personal-email']; composed most-restrictive=deny",
        v2_outcome=RuleOutcome.DENY,
        v2_matched_rule_ids=("block-personal-email",),
    )
    payload = build_policy_decided_payload(
        "email.send",
        {"to": "alice@example.com"},
        decision,
    )
    assert payload["v2_outcome"] == "deny"
    assert payload["v2_matched_rule_ids"] == ["block-personal-email"]


def test_payload_records_v2_even_when_legacy_wins() -> None:
    """Asymmetry corollary: if legacy denied but v2 said AUTO, the
    v2 fields still land in the payload so an auditor can see the
    attempted relax (FR-031 evidence trail)."""
    decision = PolicyDecision(
        decision=Decision.DENY,
        rule="untrusted-meets-egress",
        reason="rule untrusted-meets-egress fired on labels [...]",
        v2_outcome=RuleOutcome.AUTO,
        v2_matched_rule_ids=("email-to-alice-auto",),
    )
    payload = build_policy_decided_payload(
        "email.send",
        {"to": "alice@example.com"},
        decision,
    )
    assert payload["decision"] == "deny"
    assert payload["rule"] == "untrusted-meets-egress"
    assert payload["v2_outcome"] == "auto"
    assert payload["v2_matched_rule_ids"] == ["email-to-alice-auto"]
