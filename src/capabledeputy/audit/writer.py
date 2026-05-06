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
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._subscribers: list[Subscriber] = []
        self._initialized = False

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
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

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
