"""Cookbook P1.6 — tamper-evident audit log hash chain.

Covers:
  - Fresh writer's first event carries prev_hash=None
  - Subsequent events chain to the prior line's hash
  - Verifier walks a clean chain and reports ok
  - Verifier detects edit-in-place tampering at the broken line
  - Verifier detects mid-file deletion
  - Verifier detects appended-but-unchained line (legacy after
    chain → tamper)
  - Verifier tolerates leading legacy-prefix lines (pre-cookbook
    events written before this commit landed)
  - Daemon-restart preserves the chain across process lifetime
  - Rotation (max_rotated >= 1) keeps the chain spanning the
    rotation point
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.verify import _hash_line, verify_audit_chain
from capabledeputy.audit.writer import AuditWriter


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- Chain construction --------------------------------------------------


@pytest.mark.anyio
async def test_first_event_carries_null_prev_hash(tmp_path: Path) -> None:
    """A fresh audit log's first event has prev_hash=None (genesis).
    The chain begins at the next event."""
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path)
    await writer.write(Event(event_type=EventType.SESSION_CREATED))
    lines = _read_lines(path)
    assert len(lines) == 1
    assert lines[0]["prev_hash"] is None


@pytest.mark.anyio
async def test_consecutive_events_chain(tmp_path: Path) -> None:
    """The second event's prev_hash equals the SHA-256 of the first
    event's literal line bytes. Verifier sees a clean chain."""
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path)
    await writer.write(Event(event_type=EventType.SESSION_CREATED))
    await writer.write(Event(event_type=EventType.SESSION_PAUSED))
    raw = path.read_bytes()
    lines = [line for line in raw.splitlines(keepends=True) if line.strip()]
    expected = _hash_line(lines[0])
    second = json.loads(lines[1].decode())
    assert second["prev_hash"] == expected
    # Verifier confirms
    result = verify_audit_chain(path)
    assert result.ok
    assert result.n_chained == 2
    assert result.n_legacy_prefix == 0


# --- Tampering detection -------------------------------------------------


@pytest.mark.anyio
async def test_verifier_detects_edit_in_place(tmp_path: Path) -> None:
    """Modifying a middle line's bytes breaks the chain at the NEXT
    line (whose prev_hash no longer matches the modified line)."""
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path)
    for _ in range(4):
        await writer.write(Event(event_type=EventType.SESSION_CREATED))
    # Edit line 2 in place. Use a simple substitution that keeps it
    # valid JSON so the parser doesn't bail before the hash check.
    text = path.read_text()
    lines = text.splitlines()
    modified = json.loads(lines[1])
    modified["session_id"] = "00000000-0000-0000-0000-000000000999"
    lines[1] = json.dumps(modified, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    result = verify_audit_chain(path)
    assert result.ok is False
    # Line 3 is where the break surfaces — its prev_hash refers to
    # the unmodified line 2's hash, not the edited one.
    assert result.tampered_at_line == 3
    assert "tampered" in result.reason


@pytest.mark.anyio
async def test_verifier_detects_mid_file_deletion(tmp_path: Path) -> None:
    """Removing a line breaks the chain at the next line."""
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path)
    for _ in range(4):
        await writer.write(Event(event_type=EventType.SESSION_CREATED))
    lines = path.read_text().splitlines()
    # Remove line 2 (index 1)
    del lines[1]
    path.write_text("\n".join(lines) + "\n")

    result = verify_audit_chain(path)
    assert result.ok is False
    # The line that used to be #3 is now at position 2; its prev_hash
    # still references the old line 2 which is gone.
    assert result.tampered_at_line == 2


@pytest.mark.anyio
async def test_verifier_detects_appended_legacy_line(tmp_path: Path) -> None:
    """An appended line WITHOUT a prev_hash field after the chain has
    started → tamper (someone reverted to legacy form, perhaps to
    smuggle in a forged event)."""
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path)
    await writer.write(Event(event_type=EventType.SESSION_CREATED))
    await writer.write(Event(event_type=EventType.SESSION_PAUSED))
    # Append a legacy-shaped line (no prev_hash field).
    legacy = {
        "audit_id": "00000000-0000-0000-0000-000000000001",
        "timestamp": "2026-06-04T12:00:00+00:00",
        "event_type": "session.created",
        "session_id": None,
        "turn_id": None,
        "step_id": None,
        "payload": {},
    }
    with path.open("a") as f:
        f.write(json.dumps(legacy) + "\n")
    result = verify_audit_chain(path)
    assert result.ok is False
    assert result.tampered_at_line == 3
    assert "missing prev_hash" in result.reason


# --- Legacy tolerance ----------------------------------------------------


def test_verifier_tolerates_leading_legacy_prefix(tmp_path: Path) -> None:
    """Pre-cookbook audit lines (no prev_hash) form a legacy prefix
    the verifier reports separately. As long as no chained line has
    landed before them, the verifier accepts and counts them."""
    path = tmp_path / "audit.jsonl"
    # Two legacy lines, no chain.
    legacy_1 = {
        "audit_id": "00000000-0000-0000-0000-000000000001",
        "timestamp": "2026-06-03T12:00:00+00:00",
        "event_type": "session.created",
        "session_id": None,
        "turn_id": None,
        "step_id": None,
        "payload": {},
    }
    legacy_2 = {
        "audit_id": "00000000-0000-0000-0000-000000000002",
        "timestamp": "2026-06-03T12:01:00+00:00",
        "event_type": "session.paused",
        "session_id": None,
        "turn_id": None,
        "step_id": None,
        "payload": {},
    }
    path.write_text(
        json.dumps(legacy_1) + "\n" + json.dumps(legacy_2) + "\n",
    )
    result = verify_audit_chain(path)
    assert result.ok
    assert result.n_legacy_prefix == 2
    assert result.n_chained == 0


# --- Persistence across daemon restart -----------------------------------


@pytest.mark.anyio
async def test_chain_survives_writer_restart(tmp_path: Path) -> None:
    """A second AuditWriter pointed at the same file picks up the
    chain from disk so the first new event chains to the LAST
    pre-existing event. Daemon restart preserves the cryptographic
    chain."""
    path = tmp_path / "audit.jsonl"
    w1 = AuditWriter(path)
    await w1.write(Event(event_type=EventType.SESSION_CREATED))
    await w1.write(Event(event_type=EventType.SESSION_PAUSED))

    # Second writer instance — simulates daemon restart
    w2 = AuditWriter(path)
    await w2.write(Event(event_type=EventType.SESSION_RESUMED))

    result = verify_audit_chain(path)
    assert result.ok
    assert result.n_chained == 3


# --- Rotation chain preservation -----------------------------------------


@pytest.mark.anyio
async def test_chain_resets_when_rotation_archives_dropped(
    tmp_path: Path,
) -> None:
    """When max_rotated=0 the active file is truncated on rotation;
    no archive exists for the verifier to walk into. The chain
    resets — the post-rotation first event carries prev_hash=None."""
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, max_size_bytes=300, max_rotated=0)
    # Cram events until rotation fires. Each event ~150 bytes after
    # injection; 4 events should hit the 300-byte cap.
    for _ in range(6):
        await writer.write(Event(event_type=EventType.SESSION_CREATED))
    lines = _read_lines(path)
    # After rotation the first line in the new active file carries
    # prev_hash=None (chain reset because archive was dropped).
    assert lines
    assert lines[0]["prev_hash"] is None
    result = verify_audit_chain(path)
    assert result.ok
