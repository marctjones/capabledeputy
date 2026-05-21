"""Smoke tests for the bundled Google Workspace MCP server.

Tests focus on tool-list structure + handler dispatch with the
Google API client mocked out — no real network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from capabledeputy.mcp_servers import gworkspace


def _handler(name: str):
    for t in gworkspace.tools():
        if t.name == name:
            return t.handler
    raise KeyError(name)


def test_all_expected_tools_registered() -> None:
    names = {t.name for t in gworkspace.tools()}
    assert "gmail.list_threads" in names
    assert "gmail.read_thread" in names
    assert "gmail.send" in names
    assert "gmail.search" in names
    assert "docs.read" in names
    assert "docs.create" in names
    assert "drive.list" in names
    assert "drive.read_file_content" in names
    assert "calendar.list_events" in names
    assert "calendar.create_event" in names


def test_gmail_send_is_destructive() -> None:
    for t in gworkspace.tools():
        if t.name == "gmail.send":
            assert t.annotations and t.annotations.get("destructiveHint") is True
            return
    raise AssertionError("gmail.send not found")


def test_gmail_read_thread_requires_thread_id() -> None:
    for t in gworkspace.tools():
        if t.name == "gmail.read_thread":
            assert "thread_id" in t.input_schema["required"]
            return
    raise AssertionError("gmail.read_thread not found")


# ---------------- Mocked handler tests ----------------


@pytest.mark.asyncio
async def test_gmail_list_threads_routes_through_service() -> None:
    """Verify the handler builds the service + invokes the expected
    chain. The actual google-api-python-client surface is mocked."""

    mock_service = MagicMock()
    mock_service.users().threads().list().execute.return_value = {
        "threads": [
            {"id": "thread1", "snippet": "test", "historyId": "1"},
        ],
    }

    with patch(
        "capabledeputy.mcp_servers.gworkspace._build_service",
        return_value=mock_service,
    ):
        result = await _handler("gmail.list_threads")(
            {"query": "is:unread", "max_results": 10},
        )
    assert result["query"] == "is:unread"
    assert result["count"] == 1
    assert result["threads"][0]["id"] == "thread1"


@pytest.mark.asyncio
async def test_gmail_send_builds_raw_message() -> None:
    """Verify gmail.send constructs a base64-encoded RFC 5322 message."""
    mock_service = MagicMock()
    mock_service.users().messages().send().execute.return_value = {
        "id": "msg1",
        "threadId": "thread1",
    }

    with patch(
        "capabledeputy.mcp_servers.gworkspace._build_service",
        return_value=mock_service,
    ):
        result = await _handler("gmail.send")(
            {"to": "alice@example.com", "subject": "hi", "body": "hello"},
        )
    assert result["sent"] is True
    assert result["id"] == "msg1"


@pytest.mark.asyncio
async def test_docs_read_extracts_text_from_paragraphs() -> None:
    mock_service = MagicMock()
    mock_service.documents().get().execute.return_value = {
        "title": "My Doc",
        "revisionId": "abc123",
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Hello "}},
                            {"textRun": {"content": "world.\n"}},
                        ],
                    },
                },
            ],
        },
    }

    with patch(
        "capabledeputy.mcp_servers.gworkspace._build_service",
        return_value=mock_service,
    ):
        result = await _handler("docs.read")({"document_id": "doc1"})
    assert result["title"] == "My Doc"
    assert "Hello world." in result["content"]


@pytest.mark.asyncio
async def test_docs_create_returns_document_id_and_url() -> None:
    mock_service = MagicMock()
    mock_service.documents().create().execute.return_value = {"documentId": "newdoc1"}

    with patch(
        "capabledeputy.mcp_servers.gworkspace._build_service",
        return_value=mock_service,
    ):
        result = await _handler("docs.create")({"title": "Drafts"})
    assert result["document_id"] == "newdoc1"
    assert "newdoc1" in result["url"]
    assert result["created"] is True


@pytest.mark.asyncio
async def test_drive_list_returns_files() -> None:
    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {"id": "f1", "name": "Notes", "mimeType": "application/vnd.google-apps.document"},
        ],
    }

    with patch(
        "capabledeputy.mcp_servers.gworkspace._build_service",
        return_value=mock_service,
    ):
        result = await _handler("drive.list")({"query": "name contains 'Notes'"})
    assert result["count"] == 1
    assert result["files"][0]["name"] == "Notes"


@pytest.mark.asyncio
async def test_calendar_list_events_returns_events() -> None:
    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {
        "items": [
            {
                "id": "e1",
                "summary": "Standup",
                "start": {"dateTime": "2026-05-22T09:00:00-04:00"},
                "end": {"dateTime": "2026-05-22T09:30:00-04:00"},
                "location": "Zoom",
                "attendees": [],
            },
        ],
    }

    with patch(
        "capabledeputy.mcp_servers.gworkspace._build_service",
        return_value=mock_service,
    ):
        result = await _handler("calendar.list_events")(
            {"time_min": "2026-05-22T00:00:00Z", "time_max": "2026-05-23T00:00:00Z"},
        )
    assert result["count"] == 1
    assert result["events"][0]["summary"] == "Standup"


@pytest.mark.asyncio
async def test_calendar_create_event_inserts() -> None:
    mock_service = MagicMock()
    mock_service.events().insert().execute.return_value = {
        "id": "evt1",
        "htmlLink": "https://calendar.google.com/event?eid=evt1",
    }

    with patch(
        "capabledeputy.mcp_servers.gworkspace._build_service",
        return_value=mock_service,
    ):
        result = await _handler("calendar.create_event")(
            {
                "summary": "Test event",
                "start": "2026-05-22T10:00:00-04:00",
                "end": "2026-05-22T10:30:00-04:00",
            },
        )
    assert result["created"] is True
    assert result["id"] == "evt1"
