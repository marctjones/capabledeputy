"""Tests for AuditWriter's size-based rotation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter


def _make_event(payload_size: int = 200) -> Event:
    return Event(
        event_type=EventType.POLICY_DECIDED,
        session_id=uuid4(),
        turn_id=0,
        payload={"filler": "x" * payload_size},
    )


def test_no_rotation_under_threshold(tmp_path: Path) -> None:
    """Below max_size_bytes the file just grows; no rotation file exists."""
    log = tmp_path / "audit.jsonl"
    writer = AuditWriter(log, max_size_bytes=10_000, max_rotated=3)

    async def run() -> None:
        for _ in range(5):
            await writer.write(_make_event(payload_size=50))

    asyncio.run(run())
    assert log.is_file()
    assert not (tmp_path / "audit.jsonl.1").exists()
    assert log.stat().st_size > 0


def test_rotation_kicks_in_at_threshold(tmp_path: Path) -> None:
    """When a write would push the file past max_size_bytes, the
    active log is rotated to .1 before the write lands."""
    log = tmp_path / "audit.jsonl"
    # Pick a small threshold to force rotation deterministically.
    writer = AuditWriter(log, max_size_bytes=2048, max_rotated=3)

    async def run() -> None:
        for _ in range(20):
            await writer.write(_make_event(payload_size=200))

    asyncio.run(run())
    # The active log is small (most recent events); a .1 file holds
    # the rotated older portion.
    assert log.is_file()
    assert (tmp_path / "audit.jsonl.1").is_file()
    assert log.stat().st_size <= 2048 + 400  # active file under threshold (rough)


def test_max_rotated_drops_oldest(tmp_path: Path) -> None:
    """With max_rotated=2, we end up with at most active + .1 + .2."""
    log = tmp_path / "audit.jsonl"
    writer = AuditWriter(log, max_size_bytes=500, max_rotated=2)

    async def run() -> None:
        for _ in range(60):
            await writer.write(_make_event(payload_size=200))

    asyncio.run(run())
    assert log.is_file()
    assert (tmp_path / "audit.jsonl.1").is_file()
    assert (tmp_path / "audit.jsonl.2").is_file()
    # .3 must not exist — it was dropped off the back.
    assert not (tmp_path / "audit.jsonl.3").exists()


def test_tail_reads_only_active_file(tmp_path: Path) -> None:
    """After rotation, tail() reflects what's in the active file —
    older rotated events are not merged in. This is intentional:
    rotation bounds memory + disk usage; rotated files remain for
    manual inspection only."""
    log = tmp_path / "audit.jsonl"
    writer = AuditWriter(log, max_size_bytes=400, max_rotated=2)

    async def run() -> list[Event]:
        for i in range(30):
            await writer.write(_make_event(payload_size=80))
        return await writer.tail(limit=100)

    tail = asyncio.run(run())
    # tail returns whatever is in the active file — bounded by the
    # max_size_bytes, so significantly less than the 30 events written.
    assert len(tail) > 0
    assert len(tail) < 30
    # The events that ARE in tail must be the most recent ones, but we
    # can't pin exact indices without more context; the cardinality
    # check above is enough to prove rotation+truncation.


def test_max_rotated_zero_just_truncates(tmp_path: Path) -> None:
    """max_rotated=0 means "rotate by truncate, no archived files"."""
    log = tmp_path / "audit.jsonl"
    writer = AuditWriter(log, max_size_bytes=500, max_rotated=0)

    async def run() -> None:
        for _ in range(20):
            await writer.write(_make_event(payload_size=200))

    asyncio.run(run())
    assert log.is_file()
    assert not (tmp_path / "audit.jsonl.1").exists()
    assert log.stat().st_size <= 500 + 400


def test_construct_rejects_bad_args(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AuditWriter(tmp_path / "x.log", max_size_bytes=100)  # < 256
    with pytest.raises(ValueError):
        AuditWriter(tmp_path / "x.log", max_rotated=-1)
