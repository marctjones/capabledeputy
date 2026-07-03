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
    find_capability,
)
from capabledeputy.policy.labels import CategoryTag, LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier

_GLOB_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
        # WRITE_FS with allows_destructive=True so the matcher's union
        # over MODIFY_FS / DELETE_FS doesn't trigger the destructive-op
        # gate. Tests of the gate itself live in test_destructive_ops.py.
        Capability(kind=CapabilityKind.WRITE_FS, pattern="*", allows_destructive=True),
        Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*"),
        Capability(kind=CapabilityKind.GMAIL_DRAFT, pattern="*"),
        Capability(kind=CapabilityKind.SEND_MESSAGE, pattern="*"),
        Capability(kind=CapabilityKind.BROWSER_AUTOMATION, pattern="*"),
        Capability(kind=CapabilityKind.MACOS_AUTOMATION, pattern="*", allows_destructive=True),
        Capability(kind=CapabilityKind.WEB_FETCH, pattern="*"),
        Capability(kind=CapabilityKind.GENERATE_IMAGE, pattern="*"),
        Capability(kind=CapabilityKind.FETCH_IMAGE, pattern="*"),
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
    if kind == CapabilityKind.GMAIL_DRAFT:
        return Action(kind=kind, target="alice@example.com")
    if kind == CapabilityKind.SEND_MESSAGE:
        return Action(kind=kind, target="spaces/AAA")
    if kind == CapabilityKind.WEB_FETCH:
        return Action(kind=kind, target="https://example.com")
    if kind in {
        CapabilityKind.BROWSER_AUTOMATION,
        CapabilityKind.BROWSER_READ,
        CapabilityKind.BROWSER_NAVIGATE,
        CapabilityKind.BROWSER_INTERACT,
        CapabilityKind.BROWSER_SCRIPT,
        CapabilityKind.BROWSER_FILE,
    }:
        return Action(kind=kind, target="https://example.com")
    if kind in {
        CapabilityKind.MACOS_AUTOMATION,
        CapabilityKind.MACOS_APP_CONTROL,
        CapabilityKind.MACOS_CLIPBOARD_READ,
        CapabilityKind.MACOS_CLIPBOARD_WRITE,
        CapabilityKind.MACOS_NOTIFICATION,
        CapabilityKind.KEYNOTE_READ,
        CapabilityKind.KEYNOTE_PRESENT,
        CapabilityKind.PAGES_READ,
        CapabilityKind.PAGES_EDIT,
        CapabilityKind.PAGES_EXPORT,
        CapabilityKind.NUMBERS_READ,
        CapabilityKind.NUMBERS_EDIT,
        CapabilityKind.NUMBERS_EXPORT,
    }:
        return Action(kind=kind, target="com.apple.mail")
    if kind == CapabilityKind.APPLE_MAIL_DRAFT:
        return Action(kind=kind, target="alice@example.com")
    if kind == CapabilityKind.APPLE_MAIL_READ:
        return Action(kind=kind, target="inbox")
    return Action(kind=kind, target="/some/target")


def _label_state(**kwargs) -> LabelState:
    """Convert legacy label kwargs to LabelState.

    health, financial, personal → CategoryTag in axis_a
    untrusted, trusted → ProvenanceTag in axis_b
    """
    a_tags = set()
    b_tags = set()

    if kwargs.get("health"):
        a_tags.add(CategoryTag(category="health", tier=Tier.REGULATED))
    if kwargs.get("financial"):
        a_tags.add(CategoryTag(category="financial", tier=Tier.REGULATED))
    if kwargs.get("personal"):
        a_tags.add(CategoryTag(category="personal", tier=Tier.REGULATED))
    if kwargs.get("untrusted"):
        b_tags.add(ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED))
    if kwargs.get("trusted"):
        b_tags.add(ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT))

    return LabelState(
        a=frozenset(a_tags),
        b=frozenset(b_tags),
    )


def test_no_capability_denies() -> None:
    result = decide(
        capabilities=frozenset(),
        action=_action(CapabilityKind.SEND_EMAIL),
        labels=LabelState(),
    )
    assert result.decision == Decision.DENY
    assert "no matching capability" in (result.reason or "")


def test_capability_present_with_no_labels_allows() -> None:
    for kind in CapabilityKind:
        result = decide(
            capabilities=_GLOB_CAPABILITIES,
            action=_action(kind),
            labels=LabelState(),
        )
        assert result.decision == Decision.ALLOW, f"{kind} should allow with no labels"


@pytest.mark.parametrize(
    ("label_kwargs", "kind", "expected", "expected_rule"),
    [
        # Rule 1: untrusted.* + egress.* -> DENY
        (
            {"untrusted": True},
            CapabilityKind.SEND_EMAIL,
            Decision.DENY,
            "untrusted-meets-egress",
        ),
        (
            {"untrusted": True},
            CapabilityKind.SEND_EMAIL,
            Decision.DENY,
            "untrusted-meets-egress",
        ),
        (
            {"untrusted": True},
            CapabilityKind.QUEUE_PURCHASE,
            Decision.DENY,
            "untrusted-meets-egress",
        ),
        (
            {"untrusted": True},
            CapabilityKind.BROWSER_AUTOMATION,
            Decision.DENY,
            "untrusted-meets-egress",
        ),
        (
            {"untrusted": True},
            CapabilityKind.BROWSER_NAVIGATE,
            Decision.DENY,
            "untrusted-meets-egress",
        ),
        (
            {"untrusted": True},
            CapabilityKind.BROWSER_SCRIPT,
            Decision.DENY,
            "untrusted-meets-egress",
        ),
        # Rule 2: confidential.health + egress.* -> DENY
        (
            {"health": True},
            CapabilityKind.SEND_EMAIL,
            Decision.DENY,
            "health-meets-egress",
        ),
        (
            {"health": True},
            CapabilityKind.QUEUE_PURCHASE,
            Decision.DENY,
            "health-meets-egress",
        ),
        (
            {"health": True},
            CapabilityKind.BROWSER_AUTOMATION,
            Decision.DENY,
            "health-meets-egress",
        ),
        # Rule 3: confidential.financial + egress.email -> DENY
        (
            {"financial": True},
            CapabilityKind.SEND_EMAIL,
            Decision.DENY,
            "financial-meets-email",
        ),
        (
            {"financial": True},
            CapabilityKind.BROWSER_AUTOMATION,
            Decision.DENY,
            "financial-meets-email",
        ),
        (
            {"financial": True},
            CapabilityKind.BROWSER_INTERACT,
            Decision.DENY,
            "financial-meets-email",
        ),
        # Rule 4: confidential.financial + egress.purchase -> REQUIRE_APPROVAL
        (
            {"financial": True},
            CapabilityKind.QUEUE_PURCHASE,
            Decision.REQUIRE_APPROVAL,
            "financial-meets-purchase",
        ),
    ],
)
def test_rule_firings(
    label_kwargs: dict,
    kind: CapabilityKind,
    expected: Decision,
    expected_rule: str,
) -> None:
    result = decide(
        capabilities=_GLOB_CAPABILITIES,
        action=_action(kind),
        labels=_label_state(**label_kwargs),
    )
    assert result.decision == expected
    assert result.rule == expected_rule


@pytest.mark.parametrize(
    ("label_kwargs", "kind"),
    [
        ({"health": True}, CapabilityKind.READ_FS),
        ({"health": True}, CapabilityKind.WRITE_FS),
        ({"health": True}, CapabilityKind.WEB_FETCH),
        ({"health": True}, CapabilityKind.MACOS_AUTOMATION),
        ({"health": True}, CapabilityKind.BROWSER_READ),
        ({"health": True}, CapabilityKind.CALENDAR_READ),
        ({"personal": True}, CapabilityKind.SEND_EMAIL),
        ({"personal": True}, CapabilityKind.QUEUE_PURCHASE),
        ({"personal": True}, CapabilityKind.BROWSER_AUTOMATION),
        ({"personal": True}, CapabilityKind.MACOS_AUTOMATION),
        ({"trusted": True}, CapabilityKind.SEND_EMAIL),
        ({"trusted": True}, CapabilityKind.QUEUE_PURCHASE),
        ({"trusted": True}, CapabilityKind.BROWSER_AUTOMATION),
        ({"untrusted": True}, CapabilityKind.READ_FS),
        ({"untrusted": True}, CapabilityKind.WEB_FETCH),
        ({"untrusted": True}, CapabilityKind.MACOS_AUTOMATION),
        ({"financial": True}, CapabilityKind.READ_FS),
        ({"financial": True}, CapabilityKind.CALENDAR_READ),
        ({"financial": True}, CapabilityKind.MACOS_AUTOMATION),
    ],
)
def test_non_conflicting_combinations_allow(
    label_kwargs: dict,
    kind: CapabilityKind,
) -> None:
    result = decide(
        capabilities=_GLOB_CAPABILITIES,
        action=_action(kind),
        labels=_label_state(**label_kwargs),
    )
    assert result.decision == Decision.ALLOW


def test_decide_returns_matched_capability_on_allow() -> None:
    result = decide(
        capabilities=_GLOB_CAPABILITIES,
        action=_action(CapabilityKind.READ_FS),
        labels=LabelState(),
    )
    assert result.matched_capability is not None
    assert result.matched_capability.kind == CapabilityKind.READ_FS


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
        capabilities=_GLOB_CAPABILITIES,
        action=Action(
            kind=CapabilityKind.QUEUE_PURCHASE,
            target="amazon",
            amount=50,
        ),
        labels=_label_state(financial=True),
    )
    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.rule == "financial-meets-purchase"


def test_decide_purchase_over_capability_limit_denies() -> None:
    result = decide(
        capabilities=_GLOB_CAPABILITIES,
        action=Action(
            kind=CapabilityKind.QUEUE_PURCHASE,
            target="amazon",
            amount=99_999_999,
        ),
        labels=LabelState(),
    )
    assert result.decision == Decision.DENY


_TRIGGER_KWARGS = [
    {"health": True},
    {"financial": True},
    {"untrusted": True},
]


@pytest.mark.parametrize("trigger_pair", list(combinations(_TRIGGER_KWARGS, 2)))
def test_combined_triggers_still_block_egress(
    trigger_pair: tuple[dict, ...],
) -> None:
    """A label set with multiple trigger labels should still produce
    DENY (or REQUIRE_APPROVAL) for egress actions, never ALLOW."""
    # Merge the two trigger dicts
    merged_kwargs = {k: v for d in trigger_pair for k, v in d.items()}
    for kind in (CapabilityKind.SEND_EMAIL, CapabilityKind.QUEUE_PURCHASE):
        result = decide(
            capabilities=_GLOB_CAPABILITIES,
            action=_action(kind),
            labels=_label_state(**merged_kwargs),
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
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.WRITE_FS, target="/notes/x"),
        used_kinds=frozenset(),
        labels=LabelState(),
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
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.WRITE_FS, target="/notes/x"),
        used_kinds=frozenset({CapabilityKind.WEB_FETCH}),
        labels=LabelState(),
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
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.WRITE_FS, target="/notes/x"),
        used_kinds=frozenset({CapabilityKind.READ_FS, CapabilityKind.CALENDAR_READ}),
        labels=LabelState(),
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
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset({CapabilityKind.READ_FS}),
        labels=LabelState(),
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
        capabilities=frozenset({cap}),
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        used_kinds=frozenset({CapabilityKind.WEB_FETCH}),
        labels=_label_state(untrusted=True),
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
    r_future = decide(frozenset({future}), _read_action(), now=_T0, labels=LabelState())
    r_plain = decide(frozenset({plain}), _read_action(), now=_T0, labels=LabelState())
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
        frozenset({expired}),
        _read_action(),
        now=_T0 + timedelta(seconds=1),
        labels=LabelState(),
    )
    assert r.decision == Decision.DENY
    assert r.rule == CAPABILITY_EXPIRED_RULE
    assert "expired at" in (r.reason or "")


def test_half_open_boundary_instant() -> None:
    """At exactly now == expires_at the capability is expired."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*", expires_at=_T0)
    before = decide(
        frozenset({cap}),
        _read_action(),
        now=_T0 - timedelta(microseconds=1),
        labels=LabelState(),
    )
    at = decide(frozenset({cap}), _read_action(), now=_T0, labels=LabelState())
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
        frozenset({expired, live}),
        _read_action(),
        now=_T0 + timedelta(hours=1),
        labels=LabelState(),
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
        frozenset({expired}),
        _read_action(),
        now=_T0 + timedelta(seconds=1),
        labels=LabelState(),
    )
    r_absent = decide(frozenset(), _read_action(), now=_T0, labels=LabelState())
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
        frozenset({cap}),
        _read_action(),
        used_kinds=frozenset({CapabilityKind.WEB_FETCH}),
        now=_T0 + timedelta(seconds=1),
        labels=LabelState(),
    )
    assert r.decision == Decision.DENY
    assert r.rule == CAPABILITY_EXPIRED_RULE  # expiry wins (skipped first)


def test_decide_without_now_uses_wall_clock_and_stays_backcompat() -> None:
    """Omitting `now` resolves to current UTC; a non-expiring cap is
    unaffected (existing callers unchanged)."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    r = decide(frozenset({cap}), _read_action(), labels=LabelState())
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
        frozenset({cap}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
        labels=LabelState(),
    )
    assert r.decision == Decision.ALLOW


def test_rate_limit_at_limit_denies_with_rule() -> None:
    cap = _rl_cap(2, 60)
    uses = {_RL_AID: (_T0 - timedelta(seconds=30), _T0 - timedelta(seconds=5))}
    r = decide(
        frozenset({cap}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
        labels=LabelState(),
    )
    assert r.decision == Decision.DENY
    assert r.rule == RATE_LIMIT_EXCEEDED_RULE
    assert "rate limit exceeded" in (r.reason or "")


def test_rate_limit_window_slides_and_frees() -> None:
    cap = _rl_cap(1, 60)
    # one use 90s ago → outside 60s window → allowed again
    uses = {_RL_AID: (_T0 - timedelta(seconds=90),)}
    r = decide(
        frozenset({cap}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
        labels=LabelState(),
    )
    assert r.decision == Decision.ALLOW


def test_rate_exceeded_distinct_from_expired_and_no_cap() -> None:
    cap = _rl_cap(1, 60)
    uses = {_RL_AID: (_T0 - timedelta(seconds=1),)}
    r_rate = decide(
        frozenset({cap}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
        labels=LabelState(),
    )
    r_absent = decide(frozenset(), _read_action(), now=_T0, labels=LabelState())
    assert r_rate.rule == RATE_LIMIT_EXCEEDED_RULE
    assert r_absent.rule is None


def test_rate_exceeded_non_expired_sibling_survives() -> None:
    limited = _rl_cap(1, 60)
    uses = {_RL_AID: (_T0 - timedelta(seconds=1),)}
    plain = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    r = decide(
        frozenset({limited, plain}),
        _read_action(),
        now=_T0,
        cap_uses=uses,
        labels=LabelState(),
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
        frozenset({cap}),
        _read_action(),
        now=_T0 + timedelta(seconds=1),
        cap_uses=uses,
        labels=LabelState(),
    )
    assert r.rule == CAPABILITY_EXPIRED_RULE
