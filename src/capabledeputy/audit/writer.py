"""Append-only JSONL audit log writer with in-process subscriptions.

Subscribers receive events as they are written; the JSONL file is the
durable record. Live `capdep watch` connects via a subscriber; one-shot
`capdep audit` reads from the JSONL file.

Each event line carries a `prev_hash` field (cookbook P1.6 — tamper-
evident audit log) — the SHA-256 of the previous line's bytes. A
verifier can walk the chain and detect any single-line edit, insertion,
deletion, or reorder. The first line of a fresh file carries
prev_hash=None. Rotation does NOT reset the chain: the new active
file's first line carries the hash of the rotated file's final line.
The verifier today only walks the active file; cross-file verification
is a follow-up (the active-file chain is still cryptographic).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio
from anyio.to_thread import run_sync as run_in_thread

from capabledeputy.audit.events import Event

Subscriber = Callable[[Event], Awaitable[None]]


def _hash_line(line_bytes: bytes) -> str:
    """SHA-256 hex digest of one log line's bytes (with trailing
    newline). Same function used by the writer when chaining and by
    the verifier when validating — keeps the contract single-sourced."""
    return hashlib.sha256(line_bytes).hexdigest()


def _read_last_line_hash(path: Path) -> str | None:
    """Compute the hash of the last non-empty line in the file. Used
    by the writer to bootstrap _last_hash from disk on daemon
    restart so the chain spans process lifetimes. None if the file
    is empty or unreadable.

    Reads the whole file in binary so we hash the EXACT bytes that
    were written (including trailing newline) — same as what the
    next event's prev_hash will be."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    lines = raw.splitlines(keepends=True)
    for line in reversed(lines):
        # Skip blank lines (defense against partial writes / EOL
        # weirdness). A line counts as content if it has at least one
        # non-newline byte.
        if line.strip():
            return _hash_line(line)
    return None


class AuditWriter:
    """Append-only JSONL writer with size-based rotation.

    The active file is `self._path`. When a write would push the file
    past `max_size_bytes`, the writer rotates: `path` → `path.1`,
    `path.1` → `path.2`, ..., `path.{max_rotated}` is deleted. Then
    the new event is written to a fresh empty `path`.

    Rotation happens under the writer lock so subscribers see the
    write happen at a coherent moment. Tail() only reads the active
    file — older events live in the rotated files for grep but are
    not merged into in-memory reads (cheap; bounded).
    """

    def __init__(
        self,
        path: Path,
        *,
        max_size_bytes: int = 64 * 1024 * 1024,
        max_rotated: int = 3,
    ) -> None:
        if max_size_bytes < 256:
            raise ValueError("max_size_bytes must be at least 256 bytes")
        if max_rotated < 0:
            raise ValueError("max_rotated must be non-negative")
        self._path = path
        self._lock = anyio.Lock()
        self._subscribers: list[Subscriber] = []
        self._initialized = False
        self._max_size_bytes = max_size_bytes
        self._max_rotated = max_rotated
        # Cookbook P1.6 — last appended line's hash, used as prev_hash
        # on the next write. None when the file is fresh (no prior
        # event). Seeded from disk on first write if the file already
        # contains lines (daemon restart preserves the chain).
        self._last_hash: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch(exist_ok=True)
            # Seed the hash chain from the file's existing last line
            # so a daemon restart picks up where the previous run left
            # off. Empty file → _last_hash stays None (genesis).
            self._last_hash = _read_last_line_hash(self._path)
            self._initialized = True

    async def write(self, event: Event) -> None:
        await self._ensure_init()
        async with self._lock:
            # Two-phase: first decide if rotation fires (and update
            # _last_hash accordingly), then serialize so prev_hash
            # reflects the post-rotation chain state. Without this,
            # the line that triggers a rotation would still carry the
            # hash of the rotated-out line — a chain dangle.
            await run_in_thread(self._maybe_rotate_for, event)
            d = event.to_dict()
            d["prev_hash"] = self._last_hash
            line = json.dumps(d, separators=(",", ":")) + "\n"
            await run_in_thread(self._append_no_rotate, line)
            self._last_hash = _hash_line(line.encode("utf-8"))
        for sub in list(self._subscribers):
            await sub(event)

    def _maybe_rotate_for(self, event: Event) -> None:
        """Pre-write rotation check. Serializes a probe (no prev_hash
        — we don't know it yet) to estimate the line size; if the
        probe + current file size exceeds the cap, rotation fires
        BEFORE the real line is built. The estimate is a few bytes
        short of the real line (prev_hash is null in the probe vs a
        64-char hex in the real line), but that's a deliberate
        under-cap — the worst case is one event slipping past the
        cap by ~70 bytes. The size cap is forensic-bound, not
        durability-critical."""
        probe = (
            json.dumps(
                {**event.to_dict(), "prev_hash": None},
                separators=(",", ":"),
            )
            + "\n"
        )
        try:
            current_size = self._path.stat().st_size
        except FileNotFoundError:
            current_size = 0
        if current_size + len(probe.encode("utf-8")) > self._max_size_bytes:
            self._rotate()

    def _append_no_rotate(self, line: str) -> None:
        """Append after rotation has already been settled by
        _maybe_rotate_for. Pure write — no size check, no rotation."""
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _rotated_path(self, n: int) -> Path:
        """`path.suffix` is preserved at the end (`.jsonl` or whatever)
        so external tooling that sniffs by extension still works."""
        return self._path.with_suffix(self._path.suffix + f".{n}")

    def _rotate(self) -> None:
        """Shift `path.N` → `path.N+1`, dropping anything past
        `max_rotated`. The active `path` becomes `path.1`. Best-effort:
        rotation errors are swallowed so a transient FS issue can't
        block audit writes (the alternative is silently losing the
        event, which is worse for forensics).

        The hash chain spans rotation when archives are kept
        (max_rotated >= 1): the new active file's first line's
        prev_hash equals the rotated file's last line's hash (which
        is already in self._last_hash, untouched). When max_rotated
        == 0 the file is truncated and the chain resets — _last_hash
        is cleared so the next write starts a fresh genesis.
        """
        try:
            # Walk from oldest to youngest so each rename only overwrites
            # a slot we're about to abandon.
            for n in range(self._max_rotated, 0, -1):
                src = self._rotated_path(n)
                dst = self._rotated_path(n + 1)
                if src.is_file():
                    if n == self._max_rotated:
                        # Drop the oldest rotation off the back.
                        src.unlink(missing_ok=True)
                    else:
                        src.replace(dst)
            # Active → .1
            if self._max_rotated >= 1 and self._path.is_file():
                self._path.replace(self._rotated_path(1))
            else:
                # max_rotated==0 means "no archives kept"; just truncate.
                # Chain resets — no prior line exists to point at.
                self._path.unlink(missing_ok=True)
                self._last_hash = None
        except OSError:
            # Don't propagate FS errors during rotation — appending the
            # new event is still attempted on the existing/truncated file.
            pass

    def subscribe(self, sub: Subscriber) -> Callable[[], None]:
        self._subscribers.append(sub)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(sub)

        return unsubscribe

    async def read_all(self) -> list[Event]:
        if not self._path.exists():
            return []
        return await run_in_thread(self._read_all_sync)

    def _read_all_sync(self) -> list[Event]:
        events: list[Event] = []
        with self._path.open(encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if stripped:
                    events.append(Event.from_dict(json.loads(stripped)))
        return events

    async def tail(
        self,
        after_audit_id: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        events = await self.read_all()
        if after_audit_id is None:
            return events[-limit:]
        for i, ev in enumerate(events):
            if str(ev.audit_id) == after_audit_id:
                return events[i + 1 : i + 1 + limit]
        return []
