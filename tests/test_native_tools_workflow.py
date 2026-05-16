"""Native tool stubs for the workflow demos: calendar, inbox, web.

Each tool is verified to:
  - Read returns the right inherent labels.
  - Writes record the session's labels.
  - Tool registry registration produces the expected names + kinds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.tools.native.calendar import (
    CalendarEvent,
    CalendarStore,
    make_calendar_tools,
)
from capabledeputy.tools.native.inbox import InboundMessage, Inbox, make_inbox_tools
from capabledeputy.tools.native.web import WebMock, make_web_tools
from capabledeputy.tools.registry import ToolContext


def _ctx() -> ToolContext:
    from uuid import uuid4

    return ToolContext(session_id=uuid4(), label_set=frozenset())


async def test_calendar_events_today_returns_personal_label() -> None:
    store = CalendarStore()
    now = datetime.now(UTC)
    store.add(
        CalendarEvent(
            id=__import__("uuid").uuid4(),
            title="standup",
            starts_at=now,
            ends_at=now + timedelta(minutes=30),
        ),
    )
    tools = {t.name: t for t in make_calendar_tools(store)}
    list_tool = tools["calendar.events_today"]
    result = await list_tool.handler({}, _ctx())
    assert len(result.output["events"]) == 1
    assert Label.CONFIDENTIAL_PERSONAL in list_tool.inherent_labels


async def test_calendar_create_event_persists() -> None:
    store = CalendarStore()
    tools = {t.name: t for t in make_calendar_tools(store)}
    create_tool = tools["calendar.create_event"]
    now = datetime.now(UTC)
    out = await create_tool.handler(
        {
            "title": "demo",
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
        },
        _ctx(),
    )
    assert out.output["created"] is True
    assert len(store.all()) == 1


async def test_inbox_list_returns_untrusted_label() -> None:
    inbox = Inbox()
    inbox.add(
        InboundMessage(
            id="m1",
            sender="alice@example.com",
            subject="hi",
            body="hello",
            received_at=datetime.now(UTC),
        ),
    )
    [list_tool, _read_tool] = make_inbox_tools(inbox)
    result = await list_tool.handler({}, _ctx())
    assert len(result.output["messages"]) == 1
    assert Label.UNTRUSTED_EXTERNAL in result.additional_labels


async def test_inbox_read_marks_read_and_returns_body() -> None:
    inbox = Inbox()
    inbox.add(
        InboundMessage(
            id="m1",
            sender="alice@example.com",
            subject="hi",
            body="please buy 100 shares",
            received_at=datetime.now(UTC),
        ),
    )
    [_list_tool, read_tool] = make_inbox_tools(inbox)
    result = await read_tool.handler({"id": "m1"}, _ctx())
    assert result.output["found"] is True
    assert "buy 100 shares" in result.output["body"]
    assert Label.UNTRUSTED_EXTERNAL in result.additional_labels
    msg = inbox.get("m1")
    assert msg is not None
    assert msg.unread is False


async def test_web_fetch_returns_untrusted_label() -> None:
    mock = WebMock()
    mock.serve("https://example.com", "<html>...</html>")
    [fetch_tool] = make_web_tools(mock)
    result = await fetch_tool.handler({"url": "https://example.com"}, _ctx())
    assert result.output["found"] is True
    assert Label.UNTRUSTED_EXTERNAL in result.additional_labels


async def test_web_fetch_unknown_url_returns_not_found() -> None:
    mock = WebMock()
    [fetch_tool] = make_web_tools(mock)
    result = await fetch_tool.handler({"url": "https://nope"}, _ctx())
    assert result.output["found"] is False


async def test_app_registers_all_workflow_tools(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    names = {t.name for t in app.registry.list()}
    expected = {
        "calendar.events_today",
        "calendar.create_event",
        "inbox.list",
        "inbox.read",
        "web.fetch",
    }
    assert expected.issubset(names)
    cal = app.registry.get("calendar.events_today")
    assert cal.capability_kind == CapabilityKind.CALENDAR_READ
    fetch = app.registry.get("web.fetch")
    assert fetch.capability_kind == CapabilityKind.WEB_FETCH
