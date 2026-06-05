"""Improvement roadmap #8 — grant pattern validator.

Tests cover:
  - Wildcard '*' always accepted with no warnings
  - SEND_EMAIL without '@' warns
  - SEND_EMAIL with '@' accepted
  - QUEUE_PURCHASE with email-shape warns (copy-paste catch)
  - Filesystem kinds (READ_FS / CREATE_FS / etc.) without '/' warn
  - Filesystem kinds with '~' warn (no shell expansion)
  - WEB_FETCH without scheme warns
  - WEB_FETCH with '*' wildcard accepted
  - CALENDAR_* with absolute path warns
  - EXECUTE_SANDBOX with URL or path warns
  - GMAIL_READ with absolute path warns
  - Custom namespaced kinds (slack:dm.send) skip validation
  - Unrecognized kind strings skip validation (no warning, no crash)
  - All five FS kinds share the same validator
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.grant_validation import validate_grant_pattern


# --- wildcard + special-cases -------------------------------------------


def test_wildcard_accepted_for_every_kind() -> None:
    """'*' is the explicit any-match — never warn on it regardless
    of kind. The operator's intent is unambiguous."""
    for kind in CapabilityKind:
        assert validate_grant_pattern(kind, "*") == []


def test_custom_kind_skips_validation() -> None:
    """Namespaced custom kinds (slack:dm.send) have their own
    target semantics defined in servers.d/*.yaml. We don't try
    to second-guess them — empty warnings."""
    assert validate_grant_pattern("slack:dm.send", "anything") == []
    assert validate_grant_pattern("slack:dm.send", "/some/path") == []


def test_unrecognized_kind_skips_validation() -> None:
    """An unknown kind string passed by mistake doesn't crash the
    validator. The daemon will reject the grant downstream."""
    assert validate_grant_pattern("NOT_A_KIND", "anything") == []


# --- SEND_EMAIL ---------------------------------------------------------


def test_send_email_without_at_warns() -> None:
    warnings = validate_grant_pattern(CapabilityKind.SEND_EMAIL, "spouse")
    assert len(warnings) == 1
    assert "has no '@'" in warnings[0]
    assert "spouse" in warnings[0]


def test_send_email_with_at_accepted() -> None:
    assert validate_grant_pattern(CapabilityKind.SEND_EMAIL, "dad@x.com") == []
    assert (
        validate_grant_pattern(
            CapabilityKind.SEND_EMAIL,
            "*@example.com",
        )
        == []
    )


# --- QUEUE_PURCHASE -----------------------------------------------------


def test_queue_purchase_with_email_shape_warns() -> None:
    """An operator typing /grant QUEUE_PURCHASE dad@x.com almost
    certainly meant SEND_EMAIL. Flag the kind confusion."""
    warnings = validate_grant_pattern(
        CapabilityKind.QUEUE_PURCHASE,
        "dad@x.com",
    )
    assert len(warnings) == 1
    assert "email address" in warnings[0]
    assert "SEND_EMAIL" in warnings[0]


def test_queue_purchase_with_vendor_name_accepted() -> None:
    assert validate_grant_pattern(CapabilityKind.QUEUE_PURCHASE, "amazon") == []


# --- Filesystem kinds ---------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        CapabilityKind.READ_FS,
        CapabilityKind.WRITE_FS,
        CapabilityKind.CREATE_FS,
        CapabilityKind.MODIFY_FS,
        CapabilityKind.DELETE_FS,
    ],
)
def test_filesystem_relative_path_warns(kind: CapabilityKind) -> None:
    """All five filesystem kinds share the same shape constraint —
    absolute paths only."""
    warnings = validate_grant_pattern(kind, "Documents")
    assert len(warnings) == 1
    assert "not an absolute path" in warnings[0]


def test_filesystem_tilde_prefix_warns() -> None:
    """The policy engine doesn't expand '~'; matches against literal
    absolute paths. Surface this gotcha clearly."""
    warnings = validate_grant_pattern(
        CapabilityKind.READ_FS,
        "~/Documents/*",
    )
    assert len(warnings) == 1
    assert "does NOT expand '~'" in warnings[0]


def test_filesystem_absolute_path_accepted() -> None:
    for kind in (CapabilityKind.READ_FS, CapabilityKind.MODIFY_FS):
        assert validate_grant_pattern(kind, "/home/marc/Projects/*") == []


# --- WEB_FETCH ----------------------------------------------------------


def test_web_fetch_without_scheme_warns() -> None:
    warnings = validate_grant_pattern(
        CapabilityKind.WEB_FETCH,
        "example.com",
    )
    assert len(warnings) == 1
    assert "no http(s):// scheme" in warnings[0]


def test_web_fetch_with_scheme_accepted() -> None:
    assert (
        validate_grant_pattern(
            CapabilityKind.WEB_FETCH,
            "https://github.com/*",
        )
        == []
    )


def test_web_fetch_with_wildcard_accepted() -> None:
    """A bare '*' wildcard is fine even without scheme — operator
    is explicitly opting into any."""
    assert validate_grant_pattern(CapabilityKind.WEB_FETCH, "*") == []
    assert (
        validate_grant_pattern(
            CapabilityKind.WEB_FETCH,
            "*.github.com",
        )
        == []
    )  # '*' present, accepted


# --- Calendar -----------------------------------------------------------


def test_calendar_absolute_path_warns() -> None:
    warnings = validate_grant_pattern(
        CapabilityKind.CALENDAR_READ,
        "/home/marc/calendar",
    )
    assert len(warnings) == 1
    assert "filesystem path" in warnings[0]


def test_calendar_id_accepted() -> None:
    assert (
        validate_grant_pattern(
            CapabilityKind.CALENDAR_READ,
            "calendar:personal",
        )
        == []
    )


# --- Execute kinds ------------------------------------------------------


def test_execute_sandbox_path_warns() -> None:
    warnings = validate_grant_pattern(
        CapabilityKind.EXECUTE_SANDBOX,
        "/tmp/scratch",
    )
    assert len(warnings) == 1
    assert "path or URL" in warnings[0]


def test_execute_sandbox_url_warns() -> None:
    warnings = validate_grant_pattern(
        CapabilityKind.EXECUTE_DEVBOX,
        "https://example.com",
    )
    assert len(warnings) == 1


def test_execute_spec_id_accepted() -> None:
    assert (
        validate_grant_pattern(
            CapabilityKind.EXECUTE_SANDBOX,
            "scratch",
        )
        == []
    )
    assert (
        validate_grant_pattern(
            CapabilityKind.EXECUTE_DEVBOX,
            "py-dev",
        )
        == []
    )


# --- External read kinds ------------------------------------------------


def test_gmail_read_with_path_warns() -> None:
    warnings = validate_grant_pattern(
        CapabilityKind.GMAIL_READ,
        "/home/marc/inbox",
    )
    assert len(warnings) == 1
    assert "filesystem path" in warnings[0]


def test_gmail_read_with_query_accepted() -> None:
    assert (
        validate_grant_pattern(
            CapabilityKind.GMAIL_READ,
            "from:boss@example.com",
        )
        == []
    )
