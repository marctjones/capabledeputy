"""Policy kinds for semantic image generation and fetch."""

from __future__ import annotations

from uuid import uuid4

from capabledeputy.policy.capabilities import Capability, CapabilityKind, CapabilityOrigin
from capabledeputy.policy.grant_validation import validate_grant_pattern


def _cap(kind: CapabilityKind, pattern: str) -> Capability:
    return Capability(
        kind=kind,
        pattern=pattern,
        origin=CapabilityOrigin.USER_APPROVED,
        audit_id=uuid4(),
    )


def test_generate_image_grant_matches_wildcard_target() -> None:
    cap = _cap(CapabilityKind.GENERATE_IMAGE, "*")
    assert cap.matches(CapabilityKind.GENERATE_IMAGE, "*")


def test_fetch_image_grant_matches_url_target() -> None:
    cap = _cap(CapabilityKind.FETCH_IMAGE, "*")
    assert cap.matches(
        CapabilityKind.FETCH_IMAGE,
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg",
    )


def test_media_kind_grant_validation_accepts_wildcard() -> None:
    assert validate_grant_pattern(CapabilityKind.GENERATE_IMAGE, "*") == []
    assert validate_grant_pattern(CapabilityKind.FETCH_IMAGE, "*") == []
