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
