from pathlib import Path

import pytest

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.daemon.audit_handlers import make_audit_handlers


@pytest.fixture
def writer(tmp_path: Path) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


async def test_tail_returns_recent_events(writer: AuditWriter) -> None:
    handlers = make_audit_handlers(writer)
    for i in range(5):
        await writer.write(
            Event(event_type=EventType.SESSION_CREATED, payload={"i": i}),
        )
    result = await handlers["audit.tail"]({"limit": 3})
    assert len(result["events"]) == 3
    assert [e["payload"]["i"] for e in result["events"]] == [2, 3, 4]


async def test_tail_after_audit_id(writer: AuditWriter) -> None:
    handlers = make_audit_handlers(writer)
    events: list[Event] = []
    for i in range(5):
        ev = Event(event_type=EventType.SESSION_CREATED, payload={"i": i})
        events.append(ev)
        await writer.write(ev)
    result = await handlers["audit.tail"]({"after_audit_id": str(events[1].audit_id)})
    assert [e["payload"]["i"] for e in result["events"]] == [2, 3, 4]


async def test_list_filters_by_event_type(writer: AuditWriter) -> None:
    handlers = make_audit_handlers(writer)
    await writer.write(Event(event_type=EventType.SESSION_CREATED))
    await writer.write(Event(event_type=EventType.SESSION_PAUSED))
    await writer.write(Event(event_type=EventType.SESSION_CREATED))

    result = await handlers["audit.list"]({"event_type": "session.created"})
    assert len(result["events"]) == 2
    assert all(e["event_type"] == "session.created" for e in result["events"])


async def test_list_filters_by_session_id(writer: AuditWriter) -> None:
    from uuid import uuid4

    handlers = make_audit_handlers(writer)
    sid_a = uuid4()
    sid_b = uuid4()
    await writer.write(Event(event_type=EventType.SESSION_CREATED, session_id=sid_a))
    await writer.write(Event(event_type=EventType.SESSION_CREATED, session_id=sid_b))
    await writer.write(Event(event_type=EventType.SESSION_PAUSED, session_id=sid_a))

    result = await handlers["audit.list"]({"session_id": str(sid_a)})
    assert len(result["events"]) == 2
    assert all(e["session_id"] == str(sid_a) for e in result["events"])


async def test_list_combines_filters(writer: AuditWriter) -> None:
    from uuid import uuid4

    handlers = make_audit_handlers(writer)
    sid = uuid4()
    await writer.write(Event(event_type=EventType.SESSION_CREATED, session_id=sid))
    await writer.write(Event(event_type=EventType.SESSION_PAUSED, session_id=sid))
    await writer.write(Event(event_type=EventType.SESSION_CREATED))

    result = await handlers["audit.list"](
        {"event_type": "session.created", "session_id": str(sid)},
    )
    assert len(result["events"]) == 1
    assert result["events"][0]["session_id"] == str(sid)


async def test_list_respects_limit(writer: AuditWriter) -> None:
    handlers = make_audit_handlers(writer)
    for _ in range(20):
        await writer.write(Event(event_type=EventType.SESSION_CREATED))
    result = await handlers["audit.list"]({"limit": 5})
    assert len(result["events"]) == 5
