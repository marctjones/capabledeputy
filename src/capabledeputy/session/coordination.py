"""Daemon-owned coordination for multi-client session activity.

The daemon is the only process allowed to serialize turns. Clients may
observe, enqueue input, or request a turn, but they should not coordinate
with each other directly.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import anyio


@dataclass(frozen=True)
class QueuedSessionInput:
    id: str
    session_id: UUID
    message: str
    submitted_by: str
    submitted_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": str(self.session_id),
            "message": self.message,
            "submitted_by": self.submitted_by,
            "submitted_at": self.submitted_at.isoformat(),
        }


class SessionCoordinator:
    """Per-session turn locks, input queues, and replayable activity events."""

    def __init__(self, *, max_events_per_session: int = 1000) -> None:
        self._locks: dict[UUID, anyio.Lock] = {}
        self._queues: dict[UUID, deque[QueuedSessionInput]] = defaultdict(deque)
        self._events: dict[UUID, deque[dict[str, Any]]] = defaultdict(deque)
        self._next_cursor = 1
        self._max_events_per_session = max_events_per_session
        self._publisher: Any = None

    def set_publisher(self, publisher: Any) -> None:
        self._publisher = publisher

    def _lock_for(self, session_id: UUID) -> anyio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = anyio.Lock()
            self._locks[session_id] = lock
        return lock

    async def acquire_turn(self, session_id: UUID) -> anyio.Lock | None:
        lock = self._lock_for(session_id)
        try:
            lock.acquire_nowait()
        except anyio.WouldBlock:
            return None
        return lock

    def release_turn(self, session_id: UUID, lock: anyio.Lock) -> None:
        lock.release()
        if not self._queues.get(session_id) and not lock.locked():
            self._locks.pop(session_id, None)

    def enqueue_input(
        self,
        session_id: UUID,
        message: str,
        *,
        submitted_by: str = "client",
    ) -> QueuedSessionInput:
        item = QueuedSessionInput(
            id=str(uuid4()),
            session_id=session_id,
            message=message,
            submitted_by=submitted_by,
            submitted_at=datetime.now(UTC),
        )
        self._queues[session_id].append(item)
        return item

    def pop_next_input(self, session_id: UUID) -> QueuedSessionInput | None:
        queue = self._queues.get(session_id)
        if not queue:
            return None
        item = queue.popleft()
        if not queue:
            self._queues.pop(session_id, None)
        return item

    def pending_inputs(self, session_id: UUID) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._queues.get(session_id, ())]

    async def emit(
        self,
        session_id: UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "cursor": self._next_cursor,
            "session_id": str(session_id),
            "type": event_type,
            "payload": payload or {},
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._next_cursor += 1
        events = self._events[session_id]
        events.append(event)
        while len(events) > self._max_events_per_session:
            events.popleft()
        if self._publisher is not None:
            await self._publisher("session", event)
        return event

    def events_since(
        self,
        *,
        session_id: UUID | None = None,
        cursor: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        if session_id is None:
            events = [event for stream in self._events.values() for event in stream]
            events.sort(key=lambda event: int(event["cursor"]))
        else:
            events = list(self._events.get(session_id, ()))
        filtered = [event for event in events if int(event["cursor"]) > cursor]
        if limit > 0:
            filtered = filtered[:limit]
        next_cursor = cursor
        if filtered:
            next_cursor = int(filtered[-1]["cursor"])
        return {"events": filtered, "next_cursor": next_cursor}
