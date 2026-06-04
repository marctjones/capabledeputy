"""Cookbook P2.6 — rate-limit-as-friction.

Cautious sessions keep the hard DENY when a rate-limited capability
is exhausted (the cap is a non-negotiable floor). Balanced and
aggressive sessions escalate to REQUIRE_APPROVAL instead — the
operator can vouch mid-stream for one additional dispatch, useful
for catching runaway autonomous loops without losing the session.

The escalation is driven by `Session.risk_preference_at_spawn`,
threaded through `LabeledToolClient` into `engine.decide`.

Tests:
  - cautious session: rate-exceeded → DENY (back-compat preserved)
  - balanced session: rate-exceeded → REQUIRE_APPROVAL
  - aggressive session: rate-exceeded → REQUIRE_APPROVAL
  - rule name stays "rate-limit-exceeded" so audit downstream keeps
    working
  - reason text distinguishes the two paths so an operator can tell
    "escalation" from "hard deny" in the trace
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    RateLimit,
)
from capabledeputy.policy.engine import (
    RATE_LIMIT_EXCEEDED_RULE,
    decide,
)
from capabledeputy.policy.rules import Decision


def _make_cap_with_rate(
    kind: CapabilityKind = CapabilityKind.SEND_EMAIL,
    max_uses: int = 3,
    window_seconds: int = 60,
) -> Capability:
    return Capability(
        kind=kind,
        pattern="*",
        rate_limit=RateLimit(max_uses=max_uses, window_seconds=window_seconds),
    )


def _stamps_at_max(cap: Capability, now: datetime) -> tuple[datetime, ...]:
    """Cap.uses tuple with exactly max_uses entries all inside the
    window — guarantees is_rate_exceeded returns True."""
    assert cap.rate_limit is not None
    return tuple(now for _ in range(cap.rate_limit.max_uses))


def test_cautious_session_rate_exceeded_denies() -> None:
    """Default behavior — cautious sessions get hard DENY when the
    rate limit is exhausted. Preserves the back-compat contract for
    every pre-cookbook session and every test fixture."""
    now = datetime.now(UTC)
    cap = _make_cap_with_rate()
    cap_uses = {str(cap.audit_id): _stamps_at_max(cap, now)}
    result = decide(
        frozenset(),
        frozenset({cap}),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset({CapabilityKind.SEND_EMAIL}),
        now=now,
        cap_uses=cap_uses,
        rate_limit_escalation=False,
    )
    assert result.decision == Decision.DENY
    assert result.rule == RATE_LIMIT_EXCEEDED_RULE


def test_balanced_session_rate_exceeded_escalates() -> None:
    """Balanced/aggressive sessions get REQUIRE_APPROVAL instead of
    DENY. Lets the operator approve one more dispatch mid-stream —
    or, more importantly, catches a runaway loop by surfacing it as
    a visible approval card rather than a silent session-stopping
    deny."""
    now = datetime.now(UTC)
    cap = _make_cap_with_rate()
    cap_uses = {str(cap.audit_id): _stamps_at_max(cap, now)}
    result = decide(
        frozenset(),
        frozenset({cap}),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset({CapabilityKind.SEND_EMAIL}),
        now=now,
        cap_uses=cap_uses,
        rate_limit_escalation=True,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL
    # Rule name unchanged so downstream audit / recovery-step
    # synthesis still recognizes the cause.
    assert result.rule == RATE_LIMIT_EXCEEDED_RULE
    # Reason text distinguishes the escalation path so trace
    # readers (chat REPL, capdep audit) can tell the two apart.
    assert "operator approval required" in (result.reason or "").lower()


def test_under_rate_limit_unaffected_by_escalation_flag() -> None:
    """A capability that has NOT exceeded its rate limit just ALLOWs,
    regardless of the escalation flag. The escalation only kicks in
    when the rate is actually exhausted."""
    now = datetime.now(UTC)
    cap = _make_cap_with_rate(max_uses=3, window_seconds=60)
    cap_uses = {str(cap.audit_id): (now,)}  # 1 of 3 — within budget

    for flag in (False, True):
        result = decide(
            frozenset(),
            frozenset({cap}),
            Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
            used_kinds=frozenset({CapabilityKind.SEND_EMAIL}),
            now=now,
            cap_uses=cap_uses,
            rate_limit_escalation=flag,
        )
        assert result.decision == Decision.ALLOW


def test_rate_exceeded_reason_text_differs_between_paths() -> None:
    """Operator-facing audit reads the reason text. The two paths
    must be distinguishable — 'hard deny' vs 'operator approval
    required' — so a trace tells which mode fired."""
    now = datetime.now(UTC)
    cap = _make_cap_with_rate()
    cap_uses = {str(cap.audit_id): _stamps_at_max(cap, now)}
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com")

    deny = decide(
        frozenset(),
        frozenset({cap}),
        action,
        used_kinds=frozenset({CapabilityKind.SEND_EMAIL}),
        now=now,
        cap_uses=cap_uses,
        rate_limit_escalation=False,
    )
    escalate = decide(
        frozenset(),
        frozenset({cap}),
        action,
        used_kinds=frozenset({CapabilityKind.SEND_EMAIL}),
        now=now,
        cap_uses=cap_uses,
        rate_limit_escalation=True,
    )
    assert "operator approval required" not in (deny.reason or "").lower()
    assert "operator approval required" in (escalate.reason or "").lower()


@pytest.mark.parametrize(
    ("risk_preference", "expected_decision"),
    [
        ("cautious", Decision.DENY),
        ("balanced", Decision.REQUIRE_APPROVAL),
        ("aggressive", Decision.REQUIRE_APPROVAL),
    ],
)
def test_risk_preference_drives_escalation_flag(
    risk_preference: str,
    expected_decision: Decision,
) -> None:
    """Verify the mapping the tool client applies: any non-cautious
    risk preference → escalation True. The tool client computes
    `rate_limit_escalation = risk_preference != 'cautious'`."""
    flag = risk_preference != "cautious"
    now = datetime.now(UTC)
    cap = _make_cap_with_rate()
    cap_uses = {str(cap.audit_id): _stamps_at_max(cap, now)}
    result = decide(
        frozenset(),
        frozenset({cap}),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset({CapabilityKind.SEND_EMAIL}),
        now=now,
        cap_uses=cap_uses,
        rate_limit_escalation=flag,
    )
    assert result.decision == expected_decision
