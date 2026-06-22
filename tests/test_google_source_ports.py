from __future__ import annotations

import pytest

from capabledeputy.substrate.google_source import (
    GmailSourcePort,
    GoogleCalendarSourcePort,
    GoogleDriveSourcePort,
    GoogleSourcePortError,
)
from capabledeputy.substrate.source_port import get_source_port


def test_gmail_source_port_canonicalizes_message_ids_and_recipients() -> None:
    port = GmailSourcePort()

    assert port.canonicalize_resource("gmail:message:abc123") == "gmail:message:abc123"
    assert (
        port.canonicalize_resource("https://mail.google.com/mail/u/0/#inbox/FMfcgzQx")
        == "gmail:message:FMfcgzQx"
    )
    assert port.canonical_destination_id("mailto:Person@Example.com") == (
        "gmail:recipient:person@example.com"
    )


def test_gmail_source_port_fails_closed_on_ambiguous_url() -> None:
    port = GmailSourcePort()

    with pytest.raises(GoogleSourcePortError):
        port.canonicalize_resource("https://mail.google.com/mail/u/0/#inbox")


def test_drive_source_port_canonicalizes_common_google_urls() -> None:
    port = GoogleDriveSourcePort()

    assert port.canonicalize_resource("drive:file:file_123") == "google-drive:file:file_123"
    assert (
        port.canonicalize_resource("https://drive.google.com/file/d/file_123/view")
        == "google-drive:file:file_123"
    )
    assert (
        port.canonicalize_resource("https://docs.google.com/document/d/doc_456/edit")
        == "google-drive:file:doc_456"
    )
    assert (
        port.canonicalize_resource("https://drive.google.com/open?id=file_789")
        == "google-drive:file:file_789"
    )


def test_calendar_source_port_canonicalizes_ids_and_urls() -> None:
    port = GoogleCalendarSourcePort()

    assert port.canonicalize_resource("calendar:event:event_123") == (
        "google-calendar:event:event_123"
    )
    assert (
        port.canonicalize_resource("https://calendar.google.com/calendar/event?eid=event_123")
        == "google-calendar:event:event_123"
    )


def test_source_port_registry_constructs_google_ports() -> None:
    assert isinstance(get_source_port("gmail"), GmailSourcePort)
    assert isinstance(get_source_port("google-drive"), GoogleDriveSourcePort)
    assert isinstance(get_source_port("google-calendar"), GoogleCalendarSourcePort)
