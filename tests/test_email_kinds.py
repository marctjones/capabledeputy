"""Tests for granular email / drive CapabilityKinds (Issue #33 partial).

Verifies:
1. New kinds GMAIL_READ, IMAP_READ, DRIVE_READ exist
2. Capability.matches() backward-compat union: a legacy READ_FS cap
   still satisfies actions whose kind is the granular variant
3. Upstream MCP adapter's _infer_capability_kind correctly classifies
   Gmail / Drive / IMAP tool names

These are the gates that "I have READ_FS but can't read my email by
default" — fixed by adding the granular kinds + remapping.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.upstream.adapter import _infer_capability_kind


def _cap(kind: CapabilityKind, pattern: str = "*") -> Capability:
    return Capability(
        kind=kind,
        pattern=pattern,
        expiry=CapabilityExpiry.SESSION,
        origin=CapabilityOrigin.USER_APPROVED,
        audit_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_new_kinds_exist() -> None:
    """The granular email/drive kinds must be present."""
    assert CapabilityKind.GMAIL_READ.value == "GMAIL_READ"
    assert CapabilityKind.IMAP_READ.value == "IMAP_READ"
    assert CapabilityKind.DRIVE_READ.value == "DRIVE_READ"


def test_gmail_read_cap_matches_gmail_read_action() -> None:
    cap = _cap(CapabilityKind.GMAIL_READ, "*")
    assert cap.matches(CapabilityKind.GMAIL_READ, "any-target")


def test_legacy_read_fs_still_matches_gmail_action() -> None:
    """Back-compat: operators with an existing `/grant READ_FS *`
    keep working for Gmail tools. The matches() function treats
    READ_FS as a union over the granular external-read kinds."""
    cap = _cap(CapabilityKind.READ_FS, "*")
    assert cap.matches(CapabilityKind.GMAIL_READ, "any-target")
    assert cap.matches(CapabilityKind.IMAP_READ, "any-target")
    assert cap.matches(CapabilityKind.DRIVE_READ, "any-target")


def test_gmail_read_cap_does_not_match_filesystem_action() -> None:
    """Asymmetric: a GMAIL_READ cap does NOT satisfy a READ_FS action.
    Operators who grant GMAIL_READ shouldn't also be granting
    filesystem reads. The backward-compat union is one-directional:
    legacy READ_FS → granular kinds, but not the reverse."""
    cap = _cap(CapabilityKind.GMAIL_READ, "*")
    assert not cap.matches(CapabilityKind.READ_FS, "/etc/passwd")


def test_infer_gmail_read_from_name() -> None:
    """gws-mcp-server / google-mcp tool names like
    'gmail.users.messages.list' should classify as GMAIL_READ."""
    assert _infer_capability_kind(None, "gmail.users.messages.list") == CapabilityKind.GMAIL_READ
    assert _infer_capability_kind(None, "gmail.users.threads.get") == CapabilityKind.GMAIL_READ
    assert _infer_capability_kind(None, "gmail.search") == CapabilityKind.GMAIL_READ


def test_infer_gmail_send_distinguished_from_read() -> None:
    """A name containing 'gmail' AND 'send' is SEND_EMAIL, not read."""
    assert _infer_capability_kind(None, "gmail.users.messages.send") == CapabilityKind.SEND_EMAIL


def test_infer_drive_read_from_name() -> None:
    """Drive tool names like 'drive.files.list' classify as DRIVE_READ."""
    assert _infer_capability_kind(None, "drive.files.list") == CapabilityKind.DRIVE_READ
    assert _infer_capability_kind(None, "drive.search") == CapabilityKind.DRIVE_READ


def test_infer_drive_create_distinguished() -> None:
    """A Drive tool name with 'create' classifies as CREATE_FS."""
    assert _infer_capability_kind(None, "drive.files.create") == CapabilityKind.CREATE_FS


def test_infer_imap_read_from_name() -> None:
    """IMAP tool names like 'imap.fetch' / 'imap.search' classify as IMAP_READ."""
    assert _infer_capability_kind(None, "imap.fetch") == CapabilityKind.IMAP_READ
    assert _infer_capability_kind(None, "imap.search") == CapabilityKind.IMAP_READ


def test_voicemail_not_mistaken_for_gmail() -> None:
    """Earlier bug: substring 'mail' matched 'voicemail' / 'mailbox' /
    'gmail' alike. Now 'gmail' specifically routes to GMAIL_*; bare
    'mail' tools don't trigger the email branch."""
    # 'voicemail' contains 'mail' but isn't a Gmail tool — should not
    # classify as GMAIL_READ. The classifier's gmail branch checks
    # for 'gmail' specifically, so 'voicemail' falls through to
    # ordinary filesystem-style classification.
    result = _infer_capability_kind(None, "voicemail.list")
    # Should be READ_FS (or None), but definitely NOT GMAIL_READ.
    assert result != CapabilityKind.GMAIL_READ


def test_calendar_unchanged() -> None:
    """Existing CALENDAR_* classifications still work."""

    # readOnlyHint=True path
    class _Annotations:
        readOnlyHint = True  # noqa: N815 (MCP tool-annotation protocol field name)
        destructiveHint = False  # noqa: N815 (MCP tool-annotation protocol field name)

    assert (
        _infer_capability_kind(_Annotations(), "calendar.events.list")
        == CapabilityKind.CALENDAR_READ
    )
