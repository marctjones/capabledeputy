"""Tests for granular external-read CapabilityKinds (Issue #33 partial).

Verifies:
1. The granular kinds (IMAP_READ, EXTERNAL_MAIL_DRAFT, CLOUD_FILE_READ,
   APPLE_MAIL_READ) exist
2. Capability.matches() backward-compat union: a legacy READ_FS cap
   still satisfies actions whose kind is the granular variant
3. Upstream MCP adapter's _infer_capability_kind correctly classifies
   IMAP tool names

These are the gates that "I have READ_FS but can't read my email by
default" — fixed by adding the granular kinds + remapping.

Google-specific kinds (GMAIL_READ, GMAIL_DRAFT, DRIVE_READ, PEOPLE_READ)
and the adapter's gmail/drive tool-name classification were removed along
with Google Workspace integration; EXTERNAL_MAIL_DRAFT and CLOUD_FILE_READ
are the surviving generic kinds (shared with Microsoft 365).
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
    """The granular external-read/draft kinds must be present."""
    assert CapabilityKind.IMAP_READ.value == "IMAP_READ"
    assert CapabilityKind.EXTERNAL_MAIL_DRAFT.value == "EXTERNAL_MAIL_DRAFT"
    assert CapabilityKind.CLOUD_FILE_READ.value == "CLOUD_FILE_READ"
    assert CapabilityKind.APPLE_MAIL_READ.value == "APPLE_MAIL_READ"


def test_legacy_read_fs_still_matches_external_read_actions() -> None:
    """Back-compat: operators with an existing `/grant READ_FS *`
    keep working for external-read tools. The matches() function treats
    READ_FS as a union over the granular external-read kinds."""
    cap = _cap(CapabilityKind.READ_FS, "*")
    assert cap.matches(CapabilityKind.IMAP_READ, "any-target")
    assert cap.matches(CapabilityKind.CLOUD_FILE_READ, "any-target")
    assert cap.matches(CapabilityKind.APPLE_MAIL_READ, "any-target")


def test_imap_read_cap_does_not_match_filesystem_action() -> None:
    """Asymmetric: an IMAP_READ cap does NOT satisfy a READ_FS action.
    Operators who grant IMAP_READ shouldn't also be granting
    filesystem reads. The backward-compat union is one-directional:
    legacy READ_FS → granular kinds, but not the reverse."""
    cap = _cap(CapabilityKind.IMAP_READ, "*")
    assert not cap.matches(CapabilityKind.READ_FS, "/etc/passwd")


def test_infer_imap_read_from_name() -> None:
    """IMAP tool names like 'imap.fetch' / 'imap.search' classify as IMAP_READ."""
    assert _infer_capability_kind(None, "imap.fetch") == CapabilityKind.IMAP_READ
    assert _infer_capability_kind(None, "imap.search") == CapabilityKind.IMAP_READ


def test_infer_browser_and_iwork_kinds_from_name() -> None:
    assert _infer_capability_kind(None, "browser_snapshot") == CapabilityKind.BROWSER_READ
    assert _infer_capability_kind(None, "browser_navigate") == CapabilityKind.BROWSER_NAVIGATE
    assert _infer_capability_kind(None, "browser_click") == CapabilityKind.BROWSER_INTERACT
    assert _infer_capability_kind(None, "browser_evaluate") == CapabilityKind.BROWSER_SCRIPT
    assert _infer_capability_kind(None, "browser_file_upload") == CapabilityKind.BROWSER_FILE
    assert (
        _infer_capability_kind(None, "apple_mail.create_draft") == CapabilityKind.APPLE_MAIL_DRAFT
    )
    assert _infer_capability_kind(None, "keynote.slide_text") == CapabilityKind.KEYNOTE_READ
    assert _infer_capability_kind(None, "keynote.start_slideshow") == CapabilityKind.KEYNOTE_PRESENT
    assert _infer_capability_kind(None, "pages.append_text") == CapabilityKind.PAGES_EDIT
    assert _infer_capability_kind(None, "numbers.set_cell_value") == CapabilityKind.NUMBERS_EDIT
    assert _infer_capability_kind(None, "outlook.create_draft") == CapabilityKind.OUTLOOK_DRAFT
    assert _infer_capability_kind(None, "word.append_text") == CapabilityKind.WORD_EDIT
    assert _infer_capability_kind(None, "word.export_pdf") == CapabilityKind.WORD_EXPORT
    assert (
        _infer_capability_kind(None, "powerpoint.append_speaker_notes")
        == CapabilityKind.POWERPOINT_EDIT
    )
    assert (
        _infer_capability_kind(None, "powerpoint.start_slideshow")
        == CapabilityKind.POWERPOINT_PRESENT
    )


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
