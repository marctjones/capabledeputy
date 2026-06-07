"""Tests for `capdep why` (#49) decision explanation."""

from __future__ import annotations

from capabledeputy.cli.main import _explain_decision


def _decided(tool: str, decision: str, **payload) -> dict:
    return {
        "event_type": "policy.decided",
        "timestamp": "t",
        "payload": {"tool": tool, "decision": decision, **payload},
    }


def _inspector(tool: str, orig: str, adj: str, rule: str) -> dict:
    return {
        "event_type": "decision_inspector.applied",
        "payload": {
            "tool": tool,
            "original_decision": orig,
            "adjusted_decision": adj,
            "applied_rule": rule,
            "rationale": "because",
        },
    }


def test_explains_base_rule_and_reason() -> None:
    d = _decided("email.send", "deny", rule="egress-block", reason="confidential egress")
    ex = _explain_decision(d, [d])
    assert ex["decision"] == "deny"
    assert ex["rule"] == "egress-block"
    assert ex["reason"] == "confidential egress"
    assert ex["inspector_adjustment"] is None


def test_correlates_preceding_inspector_for_same_tool() -> None:
    insp = _inspector("email.send", "require_approval", "allow", "self-egress")
    d = _decided("email.send", "allow", rule="self-egress")
    events = [insp, d]
    ex = _explain_decision(d, events)
    assert ex["inspector_adjustment"] is not None
    assert ex["inspector_adjustment"]["applied_rule"] == "self-egress"
    assert ex["inspector_adjustment"]["adjusted_decision"] == "allow"


def test_does_not_cross_into_previous_decision() -> None:
    # An inspector before an EARLIER decision must not be attributed to a
    # later decision for the same tool.
    insp = _inspector("email.send", "require_approval", "allow", "old")
    earlier = _decided("email.send", "allow", rule="old")
    later = _decided("email.send", "require_approval", rule="base")
    events = [insp, earlier, later]
    ex = _explain_decision(later, events)
    assert ex["inspector_adjustment"] is None  # the earlier decision separates them


def test_inspector_for_other_tool_ignored() -> None:
    insp = _inspector("fs.read", "allow", "require_approval", "other")
    d = _decided("email.send", "allow", rule="base")
    ex = _explain_decision(d, [insp, d])
    assert ex["inspector_adjustment"] is None


def test_surfaces_v2_and_relaxation_refused() -> None:
    d = _decided(
        "email.send",
        "deny",
        rule="relax-refused",
        v2_outcome="DENY",
        v2_matched_rule_ids=["r1", "r2"],
        refused_relax_inputs=[{"x": 1}],
    )
    ex = _explain_decision(d, [d])
    assert ex["v2_outcome"] == "DENY"
    assert ex["v2_matched_rule_ids"] == ["r1", "r2"]
    assert ex["relaxation_refused"] is True
