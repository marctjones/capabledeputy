"""Append-only JSONL audit log writer with in-process subscriptions.

Subscribers receive events as they are written; the JSONL file is the
durable record. Live `capdep watch` connects via a subscriber; one-shot
`capdep audit` reads from the JSONL file.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio
from anyio.to_thread import run_sync as run_in_thread

from capabledeputy.audit.events import Event

Subscriber = Callable[[Event], Awaitable[None]]


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
            self._initialized = True

    async def write(self, event: Event) -> None:
        await self._ensure_init()
        line = json.dumps(event.to_dict(), separators=(",", ":")) + "\n"
        async with self._lock:
            await run_in_thread(self._append, line)
        for sub in list(self._subscribers):
            await sub(event)

    def _append(self, line: str) -> None:
        # Check if rotation is needed BEFORE writing.
        try:
            current_size = self._path.stat().st_size
        except FileNotFoundError:
            current_size = 0
        if current_size + len(line.encode("utf-8")) > self._max_size_bytes:
            self._rotate()
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
                self._path.unlink(missing_ok=True)
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
