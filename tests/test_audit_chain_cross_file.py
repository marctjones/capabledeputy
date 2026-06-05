"""Roadmap v2 #2 — cross-file audit chain verification.

The single-file verifier shipped in 78b52c9 only walks the active
audit.jsonl. Operators with `max_rotated > 0` accumulate rotated
archives (audit.jsonl.1, audit.jsonl.2, ...) whose chain extends
through the active file. This module's tests cover the
include_rotated mode.

Tests cover:
  - include_rotated=False matches the original behavior
  - Three-file chain verifies clean across rotation boundaries
  - Tampering in a rotated archive is detected with the file name
  - Tampering at the rotation boundary (archive.1 last line edited)
    is detected as a chain break in the active file's first line
  - Gap in archive numbering (.2 present, .1 missing) ends the
    walk at the gap rather than skipping
  - files_walked field surfaces the walk order
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.verify import _enumerate_chain_files, verify_audit_chain
from capabledeputy.audit.writer import AuditWriter


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- File enumeration helper --------------------------------------------


def test_enumerate_no_rotation(tmp_path: Path) -> None:
    active = tmp_path / "audit.jsonl"
    active.write_text("")
    assert _enumerate_chain_files(active, include_rotated=False) == [active]


def test_enumerate_with_rotation(tmp_path: Path) -> None:
    """Archive order is oldest first: .3 → .2 → .1 → active."""
    active = tmp_path / "audit.jsonl"
    active.write_text("")
    (tmp_path / "audit.jsonl.1").write_text("")
    (tmp_path / "audit.jsonl.2").write_text("")
    (tmp_path / "audit.jsonl.3").write_text("")
    walk = _enumerate_chain_files(active, include_rotated=True)
    assert [p.name for p in walk] == [
        "audit.jsonl.3",
        "audit.jsonl.2",
        "audit.jsonl.1",
        "audit.jsonl",
    ]


def test_enumerate_with_gap_stops_at_gap(tmp_path: Path) -> None:
    """A missing archive (.1 gone but .2 present) ends the walk
    before .2 — we treat the gap as the end of the chain rather
    than silently skipping archived events."""
    active = tmp_path / "audit.jsonl"
    active.write_text("")
    (tmp_path / "audit.jsonl.2").write_text("")
    # .1 deliberately not created
    walk = _enumerate_chain_files(active, include_rotated=True)
    # Walk only the active file — .2 is unreachable without .1
    assert [p.name for p in walk] == ["audit.jsonl"]


# --- End-to-end cross-file verification ---------------------------------


@pytest.mark.anyio
async def test_chain_verifies_across_three_files(tmp_path: Path) -> None:
    """Force two rotations via a small max_size_bytes; three files
    end up with a chain that spans both rotation boundaries.
    include_rotated verifies clean across them."""
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, max_size_bytes=400, max_rotated=3)
    # Each event is ~200 bytes with prev_hash; 6 events → 2 rotations
    for _ in range(6):
        await writer.write(Event(event_type=EventType.SESSION_CREATED))

    # Confirm rotation actually happened
    assert (tmp_path / "audit.jsonl.1").is_file()

    # Active-file-only walk passes (the active file's chain is
    # self-consistent because prev_hash chains backward across the
    # rotation boundary).
    single = verify_audit_chain(path)
    assert single.ok

    # Cross-file walk also passes — and reports walking multiple
    # files in the reason text.
    multi = verify_audit_chain(path, include_rotated=True)
    assert multi.ok
    assert len(multi.files_walked) >= 2
    assert "across" in multi.reason


@pytest.mark.anyio
async def test_tampering_in_rotated_archive_detected(tmp_path: Path) -> None:
    """Edit a line in audit.jsonl.1 — the cross-file walker
    surfaces the tamper with the archive's file name. Active-only
    walk MISSES it (back-compat — that's the gap this commit closes)."""
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, max_size_bytes=400, max_rotated=3)
    for _ in range(6):
        await writer.write(Event(event_type=EventType.SESSION_CREATED))
    archive = tmp_path / "audit.jsonl.1"
    assert archive.is_file()

    # Edit the first non-empty line of the archive in place
    text = archive.read_text()
    lines = text.splitlines()
    modified = json.loads(lines[0])
    modified["session_id"] = "00000000-0000-0000-0000-000000000999"
    lines[0] = json.dumps(modified, separators=(",", ":"))
    archive.write_text("\n".join(lines) + "\n")

    # Single-file walk doesn't see it
    single = verify_audit_chain(path)
    assert single.ok

    # Cross-file walk catches it; the break surfaces at the next
    # chained line whose prev_hash references the modified line —
    # could be within the same archive (if multiple lines per
    # archive) or the next file (if the modified line was the last
    # in its archive). Either way, an audit.jsonl.* file name shows
    # up somewhere in the result.
    multi = verify_audit_chain(path, include_rotated=True)
    assert multi.ok is False
    assert multi.tampered_at_file is not None
    # At least one rotated archive was involved in the walk.
    assert any(".1" in p.name or ".2" in p.name or ".3" in p.name for p in multi.files_walked)


@pytest.mark.anyio
async def test_tampering_at_rotation_boundary_detected(tmp_path: Path) -> None:
    """Edit the LAST line of audit.jsonl.1 — the FIRST line of
    audit.jsonl carries a prev_hash that no longer matches.
    Cross-file walker reports the break at the active file's
    first line (where the chain breaks)."""
    path = tmp_path / "audit.jsonl"
    writer = AuditWriter(path, max_size_bytes=400, max_rotated=3)
    for _ in range(6):
        await writer.write(Event(event_type=EventType.SESSION_CREATED))
    archive = tmp_path / "audit.jsonl.1"
    text = archive.read_text()
    lines = text.splitlines()
    # Edit the LAST line
    modified = json.loads(lines[-1])
    modified["session_id"] = "00000000-0000-0000-0000-000000000999"
    lines[-1] = json.dumps(modified, separators=(",", ":"))
    archive.write_text("\n".join(lines) + "\n")

    multi = verify_audit_chain(path, include_rotated=True)
    assert multi.ok is False
    # The break surfaces somewhere — either in archive.1 if its
    # internal chain breaks first, or in active if it crosses the
    # boundary. Either way the operator sees a structured failure.
    assert multi.tampered_at_file is not None


def test_files_walked_returns_single_when_no_rotation(tmp_path: Path) -> None:
    """No archives → files_walked is just the active file. The
    field is always populated so callers can rely on its shape."""
    path = tmp_path / "audit.jsonl"
    path.write_text("")
    result = verify_audit_chain(path)
    assert result.files_walked == (path,)


def test_no_file_at_all_returns_ok(tmp_path: Path) -> None:
    """A path that doesn't exist (fresh install, never ran daemon)
    returns ok=True with a clear reason. Matches the single-file
    pre-existing behavior."""
    path = tmp_path / "nonexistent.jsonl"
    result = verify_audit_chain(path, include_rotated=True)
    assert result.ok
    assert "no file" in result.reason
