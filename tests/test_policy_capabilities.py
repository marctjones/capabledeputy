from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
)


def test_matches_kind_and_target() -> None:
    cap = Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="*@example.com",
    )
    assert cap.matches(CapabilityKind.SEND_EMAIL, "alice@example.com")


def test_does_not_match_different_kind() -> None:
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    assert not cap.matches(CapabilityKind.WEB_FETCH, "alice@example.com")


def test_does_not_match_pattern_miss() -> None:
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com")
    assert not cap.matches(CapabilityKind.SEND_EMAIL, "bob@othersite.com")


def test_pattern_matching_is_case_sensitive() -> None:
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@Example.com")
    assert cap.matches(CapabilityKind.SEND_EMAIL, "alice@Example.com")
    assert not cap.matches(CapabilityKind.SEND_EMAIL, "alice@example.com")


def test_max_amount_within_bound_matches() -> None:
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=100,
    )
    assert cap.matches(CapabilityKind.QUEUE_PURCHASE, "amazon", amount=50)
    assert cap.matches(CapabilityKind.QUEUE_PURCHASE, "amazon", amount=100)


def test_max_amount_over_bound_does_not_match() -> None:
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=100,
    )
    assert not cap.matches(CapabilityKind.QUEUE_PURCHASE, "amazon", amount=200)


def test_max_amount_requires_amount_when_set() -> None:
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=100,
    )
    assert not cap.matches(CapabilityKind.QUEUE_PURCHASE, "amazon")


def test_no_max_amount_does_not_constrain() -> None:
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    assert cap.matches(CapabilityKind.READ_FS, "/home/marc/notes.md")


def test_round_trip_minimal() -> None:
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/marc/*")
    decoded = Capability.from_dict(cap.to_dict())
    assert decoded == cap


def test_round_trip_full() -> None:
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="amazon",
        expiry=CapabilityExpiry.ONE_SHOT,
        origin=CapabilityOrigin.USER_APPROVED,
        max_amount=100,
    )
    decoded = Capability.from_dict(cap.to_dict())
    assert decoded == cap


def test_capability_is_hashable() -> None:
    cap1 = Capability(kind=CapabilityKind.READ_FS, pattern="/a")
    cap2 = Capability(kind=CapabilityKind.READ_FS, pattern="/a")
    assert hash(cap1) != hash(cap2)
    assert cap1 != cap2


# --- Time-bounded capabilities (feature 001) -----------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402


def test_expires_at_defaults_to_none() -> None:
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    assert cap.expires_at is None
    # None ⇒ never expires, regardless of now.
    assert cap.is_expired(datetime.now(UTC)) is False


def test_is_expired_half_open_window() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*", expires_at=t0)
    assert cap.is_expired(t0 - timedelta(seconds=1)) is False  # before
    assert cap.is_expired(t0) is True  # exactly at deadline ⇒ expired
    assert cap.is_expired(t0 + timedelta(seconds=1)) is True  # after


def test_expires_at_serialization_round_trip() -> None:
    t0 = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE, pattern="amazon", expires_at=t0,
    )
    decoded = Capability.from_dict(cap.to_dict())
    assert decoded == cap
    assert decoded.expires_at == t0


def test_from_dict_without_expires_at_is_backward_tolerant() -> None:
    # A capability persisted before this feature has no expires_at key.
    legacy = {
        "kind": "READ_FS",
        "pattern": "*",
        "expiry": "session",
        "origin": "system_default",
        "audit_id": "00000000-0000-0000-0000-000000000000",
        "max_amount": None,
        "allows_destructive": False,
        "revoked_by": [],
    }
    cap = Capability.from_dict(legacy)
    assert cap.expires_at is None


def test_expiring_in_sets_deadline_now_plus_ttl() -> None:
    base = datetime(2026, 5, 1, 8, 0, 0, tzinfo=UTC)
    cap = Capability.expiring_in(
        CapabilityKind.QUEUE_PURCHASE, "amazon", timedelta(minutes=10), now=base,
    )
    assert cap.expires_at == base + timedelta(minutes=10)
    assert cap.kind == CapabilityKind.QUEUE_PURCHASE
    assert cap.pattern == "amazon"
    assert cap.is_expired(base + timedelta(minutes=9, seconds=59)) is False
    assert cap.is_expired(base + timedelta(minutes=10)) is True


def test_expiring_in_non_positive_ttl_is_already_expired() -> None:
    base = datetime(2026, 5, 1, 8, 0, 0, tzinfo=UTC)
    zero = Capability.expiring_in(
        CapabilityKind.READ_FS, "*", timedelta(0), now=base,
    )
    negative = Capability.expiring_in(
        CapabilityKind.READ_FS, "*", timedelta(seconds=-5), now=base,
    )
    # Half-open: expires_at <= now ⇒ already expired at first use.
    assert zero.is_expired(base) is True
    assert negative.is_expired(base) is True


def test_expiring_in_passes_through_other_attrs() -> None:
    cap = Capability.expiring_in(
        CapabilityKind.SEND_EMAIL,
        "*@x.com",
        timedelta(hours=1),
        now=datetime(2026, 1, 1, tzinfo=UTC),
        max_amount=None,
        allows_destructive=False,
    )
    assert cap.kind == CapabilityKind.SEND_EMAIL
    assert cap.expires_at is not None


# --- Rate-limited capabilities (feature #52) -----------------------------

from capabledeputy.policy.capabilities import RateLimit  # noqa: E402


def test_rate_limit_serialization_round_trip() -> None:
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="amazon",
        rate_limit=RateLimit(max_uses=5, window_seconds=60),
    )
    decoded = Capability.from_dict(cap.to_dict())
    assert decoded == cap
    assert decoded.rate_limit == RateLimit(5, 60)


def test_from_dict_without_rate_limit_is_backward_tolerant() -> None:
    legacy = {
        "kind": "READ_FS", "pattern": "*", "expiry": "session",
        "origin": "system_default",
        "audit_id": "00000000-0000-0000-0000-000000000000",
        "max_amount": None, "allows_destructive": False, "revoked_by": [],
    }
    assert Capability.from_dict(legacy).rate_limit is None


def test_is_rate_exceeded_counts_only_in_window() -> None:
    t0 = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    cap = Capability(
        kind=CapabilityKind.READ_FS, pattern="*",
        rate_limit=RateLimit(max_uses=2, window_seconds=60),
    )
    # two uses 90s and 80s ago → outside the 60s window → not exceeded
    old = (t0 - timedelta(seconds=90), t0 - timedelta(seconds=80))
    assert cap.is_rate_exceeded(t0, old) is False
    # two uses 30s and 10s ago → inside window, count == max → exceeded
    recent = (t0 - timedelta(seconds=30), t0 - timedelta(seconds=10))
    assert cap.is_rate_exceeded(t0, recent) is True
    # one in, one out → count 1 < 2 → not exceeded
    mixed = (t0 - timedelta(seconds=90), t0 - timedelta(seconds=10))
    assert cap.is_rate_exceeded(t0, mixed) is False


def test_no_rate_limit_never_exceeded() -> None:
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    many = tuple(datetime(2026, 1, 1, tzinfo=UTC) for _ in range(100))
    assert cap.is_rate_exceeded(datetime(2026, 1, 1, tzinfo=UTC), many) is False
