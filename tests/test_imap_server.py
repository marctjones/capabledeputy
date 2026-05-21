"""Smoke tests for the bundled IMAP/SMTP MCP server.

Tests focus on tool registration + handler dispatch with imaplib /
smtplib mocked. Real network is the manual-test concern.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from capabledeputy.mcp_servers import imap


def _handler(name: str):
    for t in imap.tools():
        if t.name == name:
            return t.handler
    raise KeyError(name)


def test_all_expected_tools_registered() -> None:
    names = {t.name for t in imap.tools()}
    assert "imap.list_threads" in names
    assert "imap.read_message" in names
    assert "imap.search" in names
    assert "imap.send" in names
    assert "imap.list_folders" in names
    assert "imap.mark_read" in names
    assert "imap.archive" in names


def test_imap_send_is_destructive() -> None:
    for t in imap.tools():
        if t.name == "imap.send":
            assert t.annotations and t.annotations.get("destructiveHint") is True
            return
    raise AssertionError("imap.send not found")


def test_imap_archive_is_destructive() -> None:
    for t in imap.tools():
        if t.name == "imap.archive":
            assert t.annotations and t.annotations.get("destructiveHint") is True
            return
    raise AssertionError("imap.archive not found")


def test_search_requires_query() -> None:
    for t in imap.tools():
        if t.name == "imap.search":
            assert "query" in t.input_schema["required"]
            return
    raise AssertionError("imap.search not found")


# ---------- _is_gmail helper ----------


def test_is_gmail_detects_gmail_hosts() -> None:
    assert imap._is_gmail("imap.gmail.com")
    assert imap._is_gmail("smtp.gmail.com")
    assert imap._is_gmail("imap.googlemail.com")
    assert not imap._is_gmail("imap.fastmail.com")
    assert not imap._is_gmail("outlook.office365.com")


# ---------- mocked handler tests ----------


@pytest.fixture
def mock_imap_cfg(monkeypatch):
    """Provide a fake load_config that returns Gmail-shaped credentials."""
    from capabledeputy.mcp_servers._imap_creds import (
        ImapConfig,
        ImapServerConfig,
        SmtpConfig,
    )

    fake_cfg = ImapServerConfig(
        imap=ImapConfig(
            host="imap.gmail.com",
            port=993,
            username="you@gmail.com",
            password="apppassword",
        ),
        smtp=SmtpConfig(
            host="smtp.gmail.com",
            port=465,
            username="you@gmail.com",
            password="apppassword",
        ),
    )
    monkeypatch.setattr(
        "capabledeputy.mcp_servers.imap.load_config",
        lambda: fake_cfg,
    )
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._imap_creds.load_config",
        lambda: fake_cfg,
    )


def _mock_imap_client():
    """Build a MagicMock that quacks like imaplib.IMAP4_SSL."""
    client = MagicMock()
    client.login.return_value = ("OK", [b"auth ok"])
    client.select.return_value = ("OK", [b"1"])
    client.logout.return_value = ("BYE", [b"bye"])
    return client


@pytest.mark.asyncio
async def test_list_threads_uses_gmail_xgm_raw(mock_imap_cfg) -> None:
    """For Gmail, list_threads with a query uses X-GM-RAW search."""
    client = _mock_imap_client()
    client.uid.return_value = ("OK", [b""])  # no UIDs found

    with patch(
        "capabledeputy.mcp_servers.imap.imaplib.IMAP4_SSL",
        return_value=client,
    ):
        result = await _handler("imap.list_threads")(
            {"query": "is:unread", "max_results": 10},
        )

    # The SEARCH call used X-GM-RAW for Gmail
    search_calls = [c for c in client.uid.call_args_list if c.args[0] == "SEARCH"]
    assert search_calls
    assert any("X-GM-RAW" in str(c.args) for c in search_calls)
    assert result["query"] == "is:unread"
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_list_threads_parses_uids_and_headers(mock_imap_cfg) -> None:
    client = _mock_imap_client()
    # First call: SEARCH returns 2 uids
    # Second & third calls: FETCH returns header bytes
    fake_headers = (
        b"From: alice@example.com\r\n"
        b"To: you@gmail.com\r\n"
        b"Subject: Test\r\n"
        b"Date: Wed, 21 May 2026 10:00:00 +0000\r\n"
        b"Message-ID: <abc123>\r\n"
        b"\r\n"
    )

    def _uid_side_effect(*args, **kwargs):
        if args[0] == "SEARCH":
            return ("OK", [b"100 101"])
        if args[0] == "FETCH":
            return ("OK", [(b"header", fake_headers)])
        return ("OK", [])

    client.uid.side_effect = _uid_side_effect

    with patch(
        "capabledeputy.mcp_servers.imap.imaplib.IMAP4_SSL",
        return_value=client,
    ):
        result = await _handler("imap.list_threads")(
            {"query": "", "max_results": 5},
        )

    assert result["count"] == 2
    assert result["messages"][0]["from"] == "alice@example.com"
    assert result["messages"][0]["subject"] == "Test"


@pytest.mark.asyncio
async def test_read_message_extracts_body(mock_imap_cfg) -> None:
    client = _mock_imap_client()
    raw_message = (
        b"From: alice@example.com\r\n"
        b"To: you@gmail.com\r\n"
        b"Subject: hi\r\n"
        b"Date: now\r\n"
        b"Message-ID: <m1>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hello, this is the body.\r\n"
    )
    client.uid.return_value = ("OK", [(b"100", raw_message)])

    with patch(
        "capabledeputy.mcp_servers.imap.imaplib.IMAP4_SSL",
        return_value=client,
    ):
        result = await _handler("imap.read_message")({"uid": "100"})

    assert result["found"] is True
    assert "Hello, this is the body." in result["body"]
    assert result["from"] == "alice@example.com"


@pytest.mark.asyncio
async def test_read_message_returns_not_found_on_no_data(mock_imap_cfg) -> None:
    client = _mock_imap_client()
    client.uid.return_value = ("OK", [])

    with patch(
        "capabledeputy.mcp_servers.imap.imaplib.IMAP4_SSL",
        return_value=client,
    ):
        result = await _handler("imap.read_message")({"uid": "999"})

    assert result["found"] is False


@pytest.mark.asyncio
async def test_send_uses_smtps_for_port_465(mock_imap_cfg) -> None:
    smtp_client = MagicMock()
    smtp_client.__enter__ = MagicMock(return_value=smtp_client)
    smtp_client.__exit__ = MagicMock(return_value=False)

    with patch(
        "capabledeputy.mcp_servers.imap.smtplib.SMTP_SSL",
        return_value=smtp_client,
    ):
        result = await _handler("imap.send")(
            {"to": "alice@example.com", "subject": "hi", "body": "hello"},
        )

    smtp_client.login.assert_called_once_with("you@gmail.com", "apppassword")
    smtp_client.send_message.assert_called_once()
    assert result["sent"] is True
    assert result["to"] == "alice@example.com"


@pytest.mark.asyncio
async def test_send_uses_starttls_for_port_587(monkeypatch) -> None:
    """When SMTP port is 587, the server should use STARTTLS not implicit SSL."""
    from capabledeputy.mcp_servers._imap_creds import (
        ImapConfig,
        ImapServerConfig,
        SmtpConfig,
    )

    fake_cfg = ImapServerConfig(
        imap=ImapConfig(
            host="imap.example.com",
            port=993,
            username="you@example.com",
            password="apppassword",
        ),
        smtp=SmtpConfig(
            host="smtp.example.com",
            port=587,
            username="you@example.com",
            password="apppassword",
        ),
    )
    monkeypatch.setattr(
        "capabledeputy.mcp_servers.imap.load_config",
        lambda: fake_cfg,
    )

    smtp_client = MagicMock()
    smtp_client.__enter__ = MagicMock(return_value=smtp_client)
    smtp_client.__exit__ = MagicMock(return_value=False)

    with patch(
        "capabledeputy.mcp_servers.imap.smtplib.SMTP",
        return_value=smtp_client,
    ):
        result = await _handler("imap.send")(
            {"to": "alice@example.com", "subject": "hi", "body": "hello"},
        )

    smtp_client.starttls.assert_called_once()
    smtp_client.login.assert_called_once()
    assert result["sent"] is True


@pytest.mark.asyncio
async def test_list_folders_parses_imap_list(mock_imap_cfg) -> None:
    client = _mock_imap_client()
    client.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "[Gmail]/Sent Mail"',
            b'(\\HasNoChildren) "/" "[Gmail]/Trash"',
        ],
    )

    with patch(
        "capabledeputy.mcp_servers.imap.imaplib.IMAP4_SSL",
        return_value=client,
    ):
        result = await _handler("imap.list_folders")({})

    assert "INBOX" in result["folders"]
    assert "[Gmail]/Sent Mail" in result["folders"]
    assert result["count"] >= 3


@pytest.mark.asyncio
async def test_archive_uses_gmail_labels_for_gmail(mock_imap_cfg) -> None:
    client = _mock_imap_client()
    client.uid.return_value = ("OK", [b"archived"])

    with patch(
        "capabledeputy.mcp_servers.imap.imaplib.IMAP4_SSL",
        return_value=client,
    ):
        result = await _handler("imap.archive")({"uid": "100"})

    # Gmail-specific: STORE -X-GM-LABELS \\Inbox
    store_calls = [c for c in client.uid.call_args_list if c.args[0] == "STORE"]
    assert store_calls
    assert any("X-GM-LABELS" in str(c.args) for c in store_calls)
    assert result["backend"] == "gmail-labels"


@pytest.mark.asyncio
async def test_mark_read_sets_seen_flag(mock_imap_cfg) -> None:
    client = _mock_imap_client()
    client.uid.return_value = ("OK", [b"flagged"])

    with patch(
        "capabledeputy.mcp_servers.imap.imaplib.IMAP4_SSL",
        return_value=client,
    ):
        result = await _handler("imap.mark_read")({"uid": "100"})

    # STORE +FLAGS (\\Seen)
    store_calls = [c for c in client.uid.call_args_list if c.args[0] == "STORE"]
    assert store_calls
    assert any("\\Seen" in str(c.args) for c in store_calls)
    assert result["marked_read"] is True
