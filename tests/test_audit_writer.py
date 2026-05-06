from pathlib import Path

import pytest

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


async def test_write_creates_file_and_persists_event(audit_path: Path) -> None:
    writer = AuditWriter(audit_path)
    await writer.write(Event(event_type=EventType.SESSION_CREATED))

    assert audit_path.exists()
    contents = audit_path.read_text("utf-8")
    assert "session.created" in contents
    assert contents.endswith("\n")


async def test_writes_are_append_only(audit_path: Path) -> None:
    writer = AuditWriter(audit_path)
    for i in range(3):
        await writer.write(
            Event(event_type=EventType.SESSION_CREATED, payload={"i": i}),
        )

    lines = audit_path.read_text("utf-8").strip().split("\n")
    assert len(lines) == 3


async def test_write_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir" / "audit.jsonl"
    writer = AuditWriter(nested)
    await writer.write(Event(event_type=EventType.SESSION_CREATED))
    assert nested.exists()


async def test_subscribers_receive_events(audit_path: Path) -> None:
    writer = AuditWriter(audit_path)
    received: list[Event] = []

    async def sub(event: Event) -> None:
        received.append(event)

    unsubscribe = writer.subscribe(sub)
    await writer.write(Event(event_type=EventType.SESSION_CREATED))
    unsubscribe()
    await writer.write(Event(event_type=EventType.SESSION_PAUSED))

    assert len(received) == 1
    assert received[0].event_type == EventType.SESSION_CREATED


async def test_unsubscribe_is_idempotent(audit_path: Path) -> None:
    writer = AuditWriter(audit_path)

    async def sub(event: Event) -> None:
        pass

    unsubscribe = writer.subscribe(sub)
    unsubscribe()
    unsubscribe()


async def test_read_all_returns_persisted_events_in_order(audit_path: Path) -> None:
    writer = AuditWriter(audit_path)
    await writer.write(Event(event_type=EventType.SESSION_CREATED, payload={"a": 1}))
    await writer.write(Event(event_type=EventType.SESSION_PAUSED, payload={"b": 2}))

    events = await writer.read_all()
    assert len(events) == 2
    assert events[0].event_type == EventType.SESSION_CREATED
    assert events[1].event_type == EventType.SESSION_PAUSED


async def test_read_all_returns_empty_for_missing_file(tmp_path: Path) -> None:
    writer = AuditWriter(tmp_path / "nonexistent.jsonl")
    events = await writer.read_all()
    assert events == []


async def test_tail_returns_recent_events(audit_path: Path) -> None:
    writer = AuditWriter(audit_path)
    for i in range(5):
        await writer.write(
            Event(event_type=EventType.SESSION_CREATED, payload={"i": i}),
        )

    last_three = await writer.tail(limit=3)
    assert len(last_three) == 3
    assert [ev.payload["i"] for ev in last_three] == [2, 3, 4]


async def test_tail_after_audit_id(audit_path: Path) -> None:
    writer = AuditWriter(audit_path)
    events: list[Event] = []
    for i in range(5):
        ev = Event(event_type=EventType.SESSION_CREATED, payload={"i": i})
        events.append(ev)
        await writer.write(ev)

    cursor = str(events[1].audit_id)
    after = await writer.tail(after_audit_id=cursor)
    assert len(after) == 3
    assert [ev.payload["i"] for ev in after] == [2, 3, 4]


async def test_tail_unknown_cursor_returns_empty(audit_path: Path) -> None:
    writer = AuditWriter(audit_path)
    await writer.write(Event(event_type=EventType.SESSION_CREATED))
    after = await writer.tail(after_audit_id="00000000-0000-0000-0000-000000000000")
    assert after == []


async def test_persistence_across_writers(audit_path: Path) -> None:
    w1 = AuditWriter(audit_path)
    await w1.write(Event(event_type=EventType.SESSION_CREATED))

    w2 = AuditWriter(audit_path)
    events = await w2.read_all()
    assert len(events) == 1
    assert events[0].event_type == EventType.SESSION_CREATED
