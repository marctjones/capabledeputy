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
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="amazon",
        expires_at=t0,
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
        CapabilityKind.QUEUE_PURCHASE,
        "amazon",
        timedelta(minutes=10),
        now=base,
    )
    assert cap.expires_at == base + timedelta(minutes=10)
    assert cap.kind == CapabilityKind.QUEUE_PURCHASE
    assert cap.pattern == "amazon"
    assert cap.is_expired(base + timedelta(minutes=9, seconds=59)) is False
    assert cap.is_expired(base + timedelta(minutes=10)) is True


def test_expiring_in_non_positive_ttl_is_already_expired() -> None:
    base = datetime(2026, 5, 1, 8, 0, 0, tzinfo=UTC)
    zero = Capability.expiring_in(
        CapabilityKind.READ_FS,
        "*",
        timedelta(0),
        now=base,
    )
    negative = Capability.expiring_in(
        CapabilityKind.READ_FS,
        "*",
        timedelta(seconds=-5),
        now=base,
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
        "kind": "READ_FS",
        "pattern": "*",
        "expiry": "session",
        "origin": "system_default",
        "audit_id": "00000000-0000-0000-0000-000000000000",
        "max_amount": None,
        "allows_destructive": False,
        "revoked_by": [],
    }
    assert Capability.from_dict(legacy).rate_limit is None


def test_is_rate_exceeded_counts_only_in_window() -> None:
    t0 = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
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


# --- 002 delegation foundational (T009/T010) ---------------------------
from uuid import uuid4  # noqa: E402

from capabledeputy.policy.capabilities import (  # noqa: E402
    DEFAULT_MAX_DELEGATION_DEPTH,
    pattern_is_subset,
)


def test_delegation_provenance_round_trip() -> None:
    pid = uuid4()
    cap = Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="mail/team/*",
        origin=CapabilityOrigin.DELEGATED,
        parent_audit_id=pid,
        depth=2,
    )
    decoded = Capability.from_dict(cap.to_dict())
    assert decoded == cap
    assert decoded.parent_audit_id == pid
    assert decoded.depth == 2
    assert decoded.origin is CapabilityOrigin.DELEGATED


def test_from_dict_without_provenance_is_backward_tolerant() -> None:
    legacy = {
        "kind": "READ_FS",
        "pattern": "*",
        "expiry": "session",
        "origin": "system_default",
        "audit_id": str(uuid4()),
        "max_amount": None,
        "allows_destructive": False,
        "revoked_by": [],
        "expires_at": None,
        "rate_limit": None,
    }
    cap = Capability.from_dict(legacy)
    assert cap.parent_audit_id is None
    assert cap.depth == 0


def test_default_max_delegation_depth_constant() -> None:
    assert DEFAULT_MAX_DELEGATION_DEPTH == 3


def test_pattern_is_subset_accepts_provable() -> None:
    assert pattern_is_subset("mail/*", "mail/*")  # exact equal
    assert pattern_is_subset("mail/team/*", "mail/*")  # narrower glob
    assert pattern_is_subset("mail/team/report", "mail/*")  # concrete
    assert pattern_is_subset("a@x.com", "*")  # under bare-* parent (pre="")


def test_pattern_is_subset_rejects_unprovable_fail_closed() -> None:
    assert not pattern_is_subset("mail/**", "mail/*")  # ** = broadening
    assert not pattern_is_subset("mail2/*", "mail/*")  # different prefix
    assert not pattern_is_subset("*", "mail/*")  # broader than parent
    assert not pattern_is_subset("mail/?", "mail/*")  # ? not provable
    assert not pattern_is_subset("x", "ma[il]/*")  # internal class in parent
    assert not pattern_is_subset("mail/a*b", "mail/*")  # non-trailing *


# --- 002 US1: derive_delegated_capability (T016 matrix, T017 FR-016) ---
from datetime import UTC as _UTC  # noqa: E402
from datetime import datetime as _dt  # noqa: E402
from datetime import timedelta as _td  # noqa: E402

from capabledeputy.policy.capabilities import (  # noqa: E402
    DelegationRefusal,
    DelegationRefusalReason,
    DelegationRequest,
    derive_delegated_capability,
)

_T = _dt(2026, 6, 1, tzinfo=_UTC)


def _parent() -> Capability:
    return Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="mail/*",
        expiry=CapabilityExpiry.SESSION,
        max_amount=100,
        expires_at=_T,
        rate_limit=RateLimit(max_uses=5, window_seconds=3600),
    )


def _derive(req: DelegationRequest, *, limit: int = 5):
    return derive_delegated_capability(_parent(), req, depth_limit=limit)


def test_derive_equal_request_clamps_to_parent() -> None:
    p = _parent()
    c = derive_delegated_capability(
        p,
        DelegationRequest(kind=CapabilityKind.SEND_EMAIL),
        depth_limit=5,
    )
    assert isinstance(c, Capability)
    assert c.pattern == "mail/*"
    assert c.max_amount == 100
    assert c.expires_at == _T
    assert c.parent_audit_id == p.audit_id
    assert c.audit_id != p.audit_id  # fresh identity
    assert c.depth == 1
    assert c.origin is CapabilityOrigin.DELEGATED


def test_derive_narrower_each_dimension_accepted() -> None:
    c = _derive(
        DelegationRequest(
            kind=CapabilityKind.SEND_EMAIL,
            pattern="mail/team/*",
            max_amount=40,
            expires_at=_T - _td(hours=1),
            rate_limit=RateLimit(max_uses=2, window_seconds=7200),
        ),
    )
    assert isinstance(c, Capability)
    assert c.pattern == "mail/team/*"
    assert c.max_amount == 40
    assert c.expires_at == _T - _td(hours=1)
    assert c.rate_limit == RateLimit(max_uses=2, window_seconds=7200)


def test_derive_widening_refused_per_dimension() -> None:
    reason = DelegationRefusalReason
    assert _derive(
        DelegationRequest(kind=CapabilityKind.SEND_EMAIL, max_amount=250),
    ) == DelegationRefusal(reason.AMOUNT_WIDENED)
    assert _derive(
        DelegationRequest(kind=CapabilityKind.SEND_EMAIL, pattern="mail/**"),
    ) == DelegationRefusal(reason.PATTERN_NOT_SUBSET)
    assert _derive(
        DelegationRequest(kind=CapabilityKind.SEND_EMAIL, expires_at=_T + _td(hours=1)),
    ) == DelegationRefusal(reason.EXPIRY_EXTENDED)
    assert _derive(
        DelegationRequest(
            kind=CapabilityKind.SEND_EMAIL,
            rate_limit=RateLimit(max_uses=99, window_seconds=3600),
        ),
    ) == DelegationRefusal(reason.RATE_LOOSENED)
    assert _derive(
        DelegationRequest(kind=CapabilityKind.WEB_FETCH),
    ) == DelegationRefusal(reason.KIND_NOT_HELD)


def test_derive_depth_exceeded() -> None:
    deep = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="mail/*", depth=5)
    out = derive_delegated_capability(
        deep,
        DelegationRequest(kind=CapabilityKind.SEND_EMAIL),
        depth_limit=5,
    )
    assert out == DelegationRefusal(DelegationRefusalReason.DEPTH_EXCEEDED)


def test_fr016_revoked_by_superset_and_lifetime_default_one_shot() -> None:
    parent = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expiry=CapabilityExpiry.SESSION,
        revoked_by=frozenset({CapabilityKind.WEB_FETCH}),
    )
    c = derive_delegated_capability(
        parent,
        DelegationRequest(
            kind=CapabilityKind.READ_FS,
            add_revoked_by=frozenset({CapabilityKind.SEND_EMAIL}),
        ),
        depth_limit=3,
    )
    assert isinstance(c, Capability)
    assert c.revoked_by >= parent.revoked_by  # superset, never narrower
    assert CapabilityKind.SEND_EMAIL in c.revoked_by
    assert c.expiry is CapabilityExpiry.ONE_SHOT  # default = most restrictive
    assert c.origin is CapabilityOrigin.DELEGATED


def test_fr016_lifetime_extended_refused() -> None:
    parent = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expiry=CapabilityExpiry.SESSION,
    )
    out = derive_delegated_capability(
        parent,
        DelegationRequest(
            kind=CapabilityKind.READ_FS,
            expiry=CapabilityExpiry.PERSISTENT,
        ),
        depth_limit=3,
    )
    assert out == DelegationRefusal(DelegationRefusalReason.LIFETIME_EXTENDED)


# --- Bare-parent escape hatch for `/path/*` patterns -----------------------
# When a grant says `READ_FS /home/marc/Projects/*` and the agent calls
# `fs.list /home/marc/Projects` (no trailing content), fnmatch rejects it.
# The semantic intent of `/foo/*` includes the entry that names the
# subtree — denying `fs.list` of that entry while permitting reads under
# it is a footgun every operator hits. Real audit-log instance:
#   policy.decided deny tool=bundled-fs.fs.list args.path=/home/marc/Projects
#                       reason="no matching capability for READ_FS()"
# despite a granted `READ_FS /home/marc/Projects/*`.


def test_glob_pattern_matches_bare_parent_directory() -> None:
    """`/foo/*` grants READ on `/foo` itself (the directory entry),
    not just its contents. This is the prerequisite for any
    list/enumerate operation."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/marc/Projects/*")
    assert cap.matches(CapabilityKind.READ_FS, "/home/marc/Projects")


def test_glob_pattern_still_matches_children() -> None:
    """Existing behavior preserved: `/foo/*` matches children too."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/marc/Projects/*")
    assert cap.matches(CapabilityKind.READ_FS, "/home/marc/Projects/weightscan")
    assert cap.matches(CapabilityKind.READ_FS, "/home/marc/Projects/weightscan/src/x.py")


def test_glob_pattern_does_not_match_sibling_directory() -> None:
    """The fix doesn't open up siblings — `/foo/*` matches `/foo` but
    NOT `/foobar` (which is a different directory whose name happens
    to share a prefix)."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/marc/Projects/*")
    assert not cap.matches(CapabilityKind.READ_FS, "/home/marc/Projectsleak")
    assert not cap.matches(CapabilityKind.READ_FS, "/home/marc/ProjectsX/y")
    assert not cap.matches(CapabilityKind.READ_FS, "/home/marc")


def test_bare_parent_only_triggers_when_pattern_ends_in_slash_star() -> None:
    """Conservative: `/foo/*.txt` is NOT a `/foo/*` pattern — the
    suffix isn't a bare `*`, so the escape hatch doesn't fire and
    `/foo` is NOT matched."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/foo/*.txt")
    assert not cap.matches(CapabilityKind.READ_FS, "/foo")
    # Children still gated by the literal pattern:
    assert cap.matches(CapabilityKind.READ_FS, "/foo/notes.txt")
    assert not cap.matches(CapabilityKind.READ_FS, "/foo/notes.md")


def test_bare_parent_does_not_fire_for_mid_pattern_wildcard() -> None:
    """Pattern `/foo/*/bar` has a wildcard in the middle, not the
    suffix. The escape hatch doesn't apply — `/foo` is NOT matched."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/foo/*/bar")
    assert not cap.matches(CapabilityKind.READ_FS, "/foo")


def test_catch_all_star_pattern_unchanged() -> None:
    """Pattern `*` already matches everything via fnmatch. The
    escape hatch is irrelevant; verify the catch-all behavior holds."""
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    assert cap.matches(CapabilityKind.READ_FS, "/home/marc/Projects")
    assert cap.matches(CapabilityKind.READ_FS, "anything-at-all")
