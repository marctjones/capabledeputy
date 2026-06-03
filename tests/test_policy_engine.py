"""Exhaustive policy engine tests over the rule matrix from DESIGN.md §7.2.

Each parametrize entry is a (labels, action_kind, expected_decision)
triple. Together they cover every interesting label-set + action-kind
combination relevant to the four canonical rules.
"""

from __future__ import annotations

from itertools import combinations

import pytest

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.engine import (
    REVOKED_BY_PRIOR_USE_RULE,
    decide,
    egress_label_for,
    find_capability,
)
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import Decision

_GLOB_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
        # WRITE_FS with allows_destructive=True so the matcher's union
        # over MODIFY_FS / DELETE_FS doesn't trigger the destructive-op
        # gate. Tests of the gate itself live in test_destructive_ops.py.
        Capability(kind=CapabilityKind.WRITE_FS, pattern="*", allows_destructive=True),
        Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*"),
        Capability(kind=CapabilityKind.WEB_FETCH, pattern="*"),
        Capability(kind=CapabilityKind.CALENDAR_READ, pattern="*"),
        Capability(
            kind=CapabilityKind.CALENDAR_WRITE,
            pattern="*",
            allows_destructive=True,
        ),
        Capability(
            kind=CapabilityKind.QUEUE_PURCHASE,
            pattern="*",
            max_amount=10_000,
        ),
        Capability(kind=CapabilityKind.EXECUTE_SANDBOX, pattern="*"),
        Capability(kind=CapabilityKind.EXECUTE_DEVBOX, pattern="*"),
    },
)


def _action(kind: CapabilityKind) -> Action:
    if kind == CapabilityKind.QUEUE_PURCHASE:
        return Action(kind=kind, target="amazon", amount=50)
    if kind == CapabilityKind.SEND_EMAIL:
        return Action(kind=kind, target="alice@example.com")
    if kind == CapabilityKind.WEB_FETCH:
        return Action(kind=kind, target="https://example.com")
    return Action(kind=kind, target="/some/target")


def test_no_capability_denies() -> None:
    result = decide(
        label_set=frozenset(),
        capabilities=frozenset(),
        action=_action(CapabilityKind.SEND_EMAIL),
    )
    assert result.decision == Decision.DENY
    assert "no matching capability" in (result.reason or "")


def test_capability_present_with_no_labels_allows() -> None:
    for kind in CapabilityKind:
        result = decide(
            label_set=frozenset(),
            capabilities=_GLOB_CAPABILITIES,
            action=_action(kind),
        )
        assert result.decision == Decision.ALLOW, f"{kind} should allow with no labels"


@pytest.mark.parametrize(
    ("labels", "kind", "expected", "expected_rule"),
    [
        # Rule 1: untrusted.* + egress.* -> DENY
        (
            frozenset({Label.UNTRUSTED_EXTERNAL}),
            CapabilityKind.SEND_EMAIL,
            Decision.DENY,
            "untrusted-meets-egress",
        ),
        (
            frozenset({Label.UNTRUSTED_USER_INPUT}),
            CapabilityKind.SEND_EMAIL,
            Decision.DENY,
            "untrusted-meets-egress",
        ),
        (
            frozenset({Label.UNTRUSTED_EXTERNAL}),
            CapabilityKind.QUEUE_PURCHASE,
            Decision.DENY,
            "untrusted-meets-egress",
        ),
        # Rule 2: confidential.health + egress.* -> DENY
        (
            frozenset({Label.CONFIDENTIAL_HEALTH}),
            CapabilityKind.SEND_EMAIL,
            Decision.DENY,
            "health-meets-egress",
        ),
        (
            frozenset({Label.CONFIDENTIAL_HEALTH}),
            CapabilityKind.QUEUE_PURCHASE,
            Decision.DENY,
            "health-meets-egress",
        ),
        # Rule 3: confidential.financial + egress.email -> DENY
        (
            frozenset({Label.CONFIDENTIAL_FINANCIAL}),
            CapabilityKind.SEND_EMAIL,
            Decision.DENY,
            "financial-meets-email",
        ),
        # Rule 4: confidential.financial + egress.purchase -> REQUIRE_APPROVAL
        (
            frozenset({Label.CONFIDENTIAL_FINANCIAL}),
            CapabilityKind.QUEUE_PURCHASE,
            Decision.REQUIRE_APPROVAL,
            "financial-meets-purchase",
        ),
    ],
)
def test_rule_firings(
    labels: frozenset[Label],
    kind: CapabilityKind,
    expected: Decision,
    expected_rule: str,
) -> None:
    result = decide(
        label_set=labels,
        capabilities=_GLOB_CAPABILITIES,
        action=_action(kind),
    )
    assert result.decision == expected
    assert result.rule == expected_rule


@pytest.mark.parametrize(
    ("labels", "kind"),
    [
        (frozenset({Label.CONFIDENTIAL_HEALTH}), CapabilityKind.READ_FS),
        (frozenset({Label.CONFIDENTIAL_HEALTH}), CapabilityKind.WRITE_FS),
        (frozenset({Label.CONFIDENTIAL_HEALTH}), CapabilityKind.WEB_FETCH),
        (frozenset({Label.CONFIDENTIAL_HEALTH}), CapabilityKind.CALENDAR_READ),
        (frozenset({Label.CONFIDENTIAL_PERSONAL}), CapabilityKind.SEND_EMAIL),
        (frozenset({Label.CONFIDENTIAL_PERSONAL}), CapabilityKind.QUEUE_PURCHASE),
        (frozenset({Label.TRUSTED_USER_DIRECT}), CapabilityKind.SEND_EMAIL),
        (frozenset({Label.TRUSTED_USER_DIRECT}), CapabilityKind.QUEUE_PURCHASE),
        (frozenset({Label.UNTRUSTED_EXTERNAL}), CapabilityKind.READ_FS),
        (frozenset({Label.UNTRUSTED_EXTERNAL}), CapabilityKind.WEB_FETCH),
        (frozenset({Label.CONFIDENTIAL_FINANCIAL}), CapabilityKind.READ_FS),
        (frozenset({Label.CONFIDENTIAL_FINANCIAL}), CapabilityKind.CALENDAR_READ),
    ],
)
def test_non_conflicting_combinations_allow(
    labels: frozenset[Label],
    kind: CapabilityKind,
) -> None:
    result = decide(
        label_set=labels,
        capabilities=_GLOB_CAPABILITIES,
        action=_action(kind),
    )
    assert result.decision == Decision.ALLOW


def test_decide_returns_matched_capability_on_allow() -> None:
    result = decide(
        label_set=frozenset(),
        capabilities=_GLOB_CAPABILITIES,
        action=_action(CapabilityKind.READ_FS),
    )
    assert result.matched_capability is not None
    assert result.matched_capability.kind == CapabilityKind.READ_FS


def test_decide_includes_egress_label_in_effective_labels() -> None:
    result = decide(
        label_set=frozenset({Label.CONFIDENTIAL_PERSONAL}),
        capabilities=_GLOB_CAPABILITIES,
        action=_action(CapabilityKind.SEND_EMAIL),
    )
    assert Label.EGRESS_EMAIL in result.effective_labels
    assert Label.CONFIDENTIAL_PERSONAL in result.effective_labels


def test_decide_does_not_add_egress_for_non_egress_actions() -> None:
    result = decide(
        label_set=frozenset(),
        capabilities=_GLOB_CAPABILITIES,
        action=_action(CapabilityKind.READ_FS),
    )
    assert result.effective_labels == frozenset()


def test_egress_label_lookup() -> None:
    assert egress_label_for(CapabilityKind.SEND_EMAIL) == Label.EGRESS_EMAIL
    assert egress_label_for(CapabilityKind.QUEUE_PURCHASE) == Label.EGRESS_PURCHASE
    assert egress_label_for(CapabilityKind.READ_FS) is None
    assert egress_label_for(CapabilityKind.CALENDAR_READ) is None


def test_find_capability_returns_first_match() -> None:
    cap1 = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    cap2 = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    found = find_capability(
        frozenset({cap1, cap2}),
        Action(kind=CapabilityKind.READ_FS, target="/home/marc/notes"),
    )
    assert found is not None
    assert found.kind == CapabilityKind.READ_FS


def test_find_capability_returns_none_when_no_match() -> None:
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    found = find_capability(
        frozenset({cap}),
        Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com"),
    )
    assert found is None


def test_find_capability_respects_max_amount() -> None:
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=100,
    )
    found = find_capability(
        frozenset({cap}),
        Action(kind=CapabilityKind.QUEUE_PURCHASE, target="amazon", amount=200),
    )
    assert found is None


def test_decide_purchase_below_limit_with_financial_requires_approval() -> None:
    result = decide(
        label_set=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        capabilities=_GLOB_CAPABILITIES,
        action=Action(
            kind=CapabilityKind.QUEUE_PURCHASE,
            target="amazon",
            amount=50,
        ),
    )
    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.rule == "financial-meets-purchase"


def test_decide_purchase_over_capability_limit_denies() -> None:
    result = decide(
        label_set=frozenset(),
        capabilities=_GLOB_CAPABILITIES,
        action=Action(
            kind=CapabilityKind.QUEUE_PURCHASE,
            target="amazon",
            amount=99_999_999,
        ),
    )
    assert result.decision == Decision.DENY


_TRIGGER_LABELS = (
    Label.CONFIDENTIAL_HEALTH,
    Label.CONFIDENTIAL_FINANCIAL,
    Label.UNTRUSTED_EXTERNAL,
    Label.UNTRUSTED_USER_INPUT,
)


@pytest.mark.parametrize("trigger_pair", list(combinations(_TRIGGER_LABELS, 2)))
def test_combined_triggers_still_block_egress(
    trigger_pair: tuple[Label, ...],
) -> None:
    """A label set with multiple trigger labels should still produce
    DENY (or REQUIRE_APPROVAL) for egress actions, never ALLOW."""
    labels = frozenset(trigger_pair)
    for kind in (CapabilityKind.SEND_EMAIL, CapabilityKind.QUEUE_PURCHASE):
        result = decide(
            label_set=labels,
            capabilities=_GLOB_CAPABILITIES,
            action=_action(kind),
        )
        assert result.decision in {Decision.DENY, Decision.REQUIRE_APPROVAL}, (
            f"{trigger_pair} + {kind} → {result.decision} (expected non-ALLOW)"
        )


# Tool-identity revocation primitive: a Capability declares
# revoked_by={K1, K2, ...}; if any of those kinds appears in the
# session's used_kinds, the capability is treated as revoked.


def test_revoked_by_empty_used_kinds_allows() -> None:
    cap = Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        allows_destructive=True,
        revoked_by=frozenset({CapabilityKind.WEB_FETCH}),
    )
    result = decide(
        label_set=frozenset(),
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.WRITE_FS, target="/notes/x"),
        used_kinds=frozenset(),
    )
    assert result.decision == Decision.ALLOW


def test_revoked_by_prior_use_denies() -> None:
    cap = Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        allows_destructive=True,
        revoked_by=frozenset({CapabilityKind.WEB_FETCH}),
    )
    result = decide(
        label_set=frozenset(),
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.WRITE_FS, target="/notes/x"),
        used_kinds=frozenset({CapabilityKind.WEB_FETCH}),
    )
    assert result.decision == Decision.DENY
    assert result.rule == REVOKED_BY_PRIOR_USE_RULE
    assert "WEB_FETCH" in (result.reason or "")
    assert result.matched_capability is cap


def test_revoked_by_unrelated_prior_use_allows() -> None:
    cap = Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        allows_destructive=True,
        revoked_by=frozenset({CapabilityKind.WEB_FETCH}),
    )
    result = decide(
        label_set=frozenset(),
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.WRITE_FS, target="/notes/x"),
        used_kinds=frozenset({CapabilityKind.READ_FS, CapabilityKind.CALENDAR_READ}),
    )
    assert result.decision == Decision.ALLOW


def test_revoked_by_any_member_of_set_denies() -> None:
    cap = Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="*",
        revoked_by=frozenset(
            {CapabilityKind.WEB_FETCH, CapabilityKind.READ_FS},
        ),
    )
    result = decide(
        label_set=frozenset(),
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset({CapabilityKind.READ_FS}),
    )
    assert result.decision == Decision.DENY
    assert result.rule == REVOKED_BY_PRIOR_USE_RULE


def test_revoked_by_runs_before_conflict_rules() -> None:
    """If both revocation and a conflict rule would fire, revocation
    wins — the matched capability is gone, so there is no flow to
    even consider."""
    cap = Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="*",
        revoked_by=frozenset({CapabilityKind.WEB_FETCH}),
    )
    result = decide(
        # untrusted.external would normally fire untrusted-meets-egress
        label_set=frozenset({Label.UNTRUSTED_EXTERNAL}),
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset({CapabilityKind.WEB_FETCH}),
    )
    assert result.decision == Decision.DENY
    assert result.rule == REVOKED_BY_PRIOR_USE_RULE


def test_revoked_by_serializes_round_trip() -> None:
    cap = Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="/notes/*",
        revoked_by=frozenset(
            {CapabilityKind.WEB_FETCH, CapabilityKind.READ_FS},
        ),
    )
    restored = Capability.from_dict(cap.to_dict())
    assert restored.revoked_by == cap.revoked_by


def test_default_revoked_by_is_empty() -> None:
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    assert cap.revoked_by == frozenset()


# --- Time-bounded capabilities (feature 001, US1) ------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from capabledeputy.policy.engine import CAPABILITY_EXPIRED_RULE  # noqa: E402

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _read_action() -> Action:
    return Action(kind=CapabilityKind.READ_FS, target="/x")


def test_future_deadline_is_transparent() -> None:
    """C2: a not-yet-expired cap decides identically to a non-expiring
    one."""
    future = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=_T0 + timedelta(hours=1),
    )
    plain = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    r_future = decide(frozenset(), frozenset({future}), _read_action(), now=_T0)
    r_plain = decide(frozenset(), frozenset({plain}), _read_action(), now=_T0)
    assert r_future.decision == r_plain.decision == Decision.ALLOW


def test_expired_capability_treated_as_absent() -> None:
    """C3: a past-deadline cap is skipped; with no other cap the
    denial is attributed to expiry, not generic no-capability."""
    expired = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=_T0,
    )
    r = decide(
        frozenset(),
        frozenset({expired}),
        _read_action(),
        now=_T0 + timedelta(seconds=1),
    )
    assert r.decision == Decision.DENY
    assert r.rule == CAPABILITY_EXPIRED_RULE
    assert "expired at" in (r.reason or "")


def test_half_open_boundary_instant() -> None:
    """At exactly now == expires_at the capability is expired."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*", expires_at=_T0)
    before = decide(
        frozenset(),
        frozenset({cap}),
        _read_action(),
        now=_T0 - timedelta(microseconds=1),
    )
    at = decide(frozenset(), frozenset({cap}), _read_action(), now=_T0)
    assert before.decision == Decision.ALLOW
    assert at.decision == Decision.DENY
    assert at.rule == CAPABILITY_EXPIRED_RULE


def test_non_expired_sibling_still_satisfies() -> None:
    """C5: an expired cap is inert, not poisonous — a non-expired
    sibling matching the same action still allows."""
    expired = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=_T0,
    )
    live = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    r = decide(
        frozenset(),
        frozenset({expired, live}),
        _read_action(),
        now=_T0 + timedelta(hours=1),
    )
    assert r.decision == Decision.ALLOW


def test_expiry_distinct_from_no_capability() -> None:
    """C4: expired-only denial says capability-expired; truly-absent
    says the generic no-capability reason."""
    expired = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=_T0,
    )
    r_expired = decide(
        frozenset(),
        frozenset({expired}),
        _read_action(),
        now=_T0 + timedelta(seconds=1),
    )
    r_absent = decide(frozenset(), frozenset(), _read_action(), now=_T0)
    assert r_expired.rule == CAPABILITY_EXPIRED_RULE
    assert r_absent.rule is None
    assert "no matching capability" in (r_absent.reason or "")


def test_expiry_composes_with_revoked_by() -> None:
    """C7: any single disqualifier makes a cap unusable. An expired
    cap is skipped before revoked_by is even consulted; result is
    still a deterministic deny."""
    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=_T0,
        revoked_by=frozenset({CapabilityKind.WEB_FETCH}),
    )
    r = decide(
        frozenset(),
        frozenset({cap}),
        _read_action(),
        used_kinds=frozenset({CapabilityKind.WEB_FETCH}),
        now=_T0 + timedelta(seconds=1),
    )
    assert r.decision == Decision.DENY
    assert r.rule == CAPABILITY_EXPIRED_RULE  # expiry wins (skipped first)


def test_decide_without_now_uses_wall_clock_and_stays_backcompat() -> None:
    """Omitting `now` resolves to current UTC; a non-expiring cap is
    unaffected (existing callers unchanged)."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    r = decide(frozenset(), frozenset({cap}), _read_action())
    assert r.decision == Decision.ALLOW


# --- Rate-limited capabilities (feature #52) -----------------------------

from capabledeputy.policy.capabilities import RateLimit  # noqa: E402
from capabledeputy.policy.engine import RATE_LIMIT_EXCEEDED_RULE  # noqa: E402

_RL_AID = "11111111-1111-1111-1111-111111111111"


def _rl_cap(max_uses: int, window: int) -> Capability:
    from uuid import UUID

    return Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        audit_id=UUID(_RL_AID),
        rate_limit=RateLimit(max_uses=max_uses, window_seconds=window),
    )


def test_rate_limit_under_limit_allows() -> None:
    cap = _rl_cap(3, 60)
    uses = {_RL_AID: (_T0 - timedelta(seconds=10),)}  # 1 use < 3
    r = decide(
        frozenset(),
        frozenset({cap}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
    )
    assert r.decision == Decision.ALLOW


def test_rate_limit_at_limit_denies_with_rule() -> None:
    cap = _rl_cap(2, 60)
    uses = {_RL_AID: (_T0 - timedelta(seconds=30), _T0 - timedelta(seconds=5))}
    r = decide(
        frozenset(),
        frozenset({cap}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
    )
    assert r.decision == Decision.DENY
    assert r.rule == RATE_LIMIT_EXCEEDED_RULE
    assert "rate limit exceeded" in (r.reason or "")


def test_rate_limit_window_slides_and_frees() -> None:
    cap = _rl_cap(1, 60)
    # one use 90s ago → outside 60s window → allowed again
    uses = {_RL_AID: (_T0 - timedelta(seconds=90),)}
    r = decide(
        frozenset(),
        frozenset({cap}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
    )
    assert r.decision == Decision.ALLOW


def test_rate_exceeded_distinct_from_expired_and_no_cap() -> None:
    cap = _rl_cap(1, 60)
    uses = {_RL_AID: (_T0 - timedelta(seconds=1),)}
    r_rate = decide(
        frozenset(),
        frozenset({cap}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
    )
    r_absent = decide(frozenset(), frozenset(), _read_action(), now=_T0)
    assert r_rate.rule == RATE_LIMIT_EXCEEDED_RULE
    assert r_absent.rule is None


def test_rate_exceeded_non_expired_sibling_survives() -> None:
    limited = _rl_cap(1, 60)
    uses = {_RL_AID: (_T0 - timedelta(seconds=1),)}
    plain = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    r = decide(
        frozenset(),
        frozenset({limited, plain}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
    )
    assert r.decision == Decision.ALLOW  # sibling without a limit


def test_expiry_takes_precedence_over_rate_in_attribution() -> None:
    """A cap both expired and rate-exceeded is reported as expired
    (it's gone entirely); rate-limit attribution only when the cap is
    live but throttled."""
    from uuid import UUID

    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        audit_id=UUID(_RL_AID),
        expires_at=_T0,
        rate_limit=RateLimit(max_uses=1, window_seconds=60),
    )
    uses = {_RL_AID: (_T0 - timedelta(seconds=1),)}
    r = decide(
        frozenset(),
        frozenset({cap}),
        _read_action(),
        now=_T0 + timedelta(seconds=1),
        cap_uses=uses,
    )
    assert r.rule == CAPABILITY_EXPIRED_RULE
