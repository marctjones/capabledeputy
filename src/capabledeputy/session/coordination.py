"""Daemon-owned coordination for multi-client session activity.

The daemon is the only process allowed to serialize turns. Clients may
observe, enqueue input, or request a turn, but they should not coordinate
with each other directly.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
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


class WorkstreamOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class InteractiveWorkstream:
    id: str
    session_id: UUID
    client_id: str
    lease_token: str
    claimed_at: datetime
    lease_until: datetime
    status: str = "active"
    reason: str | None = None
    released_at: datetime | None = None
    last_renewed_at: datetime | None = None

    def is_active(self, *, now: datetime | None = None) -> bool:
        return self.status == "active" and self.lease_until > (now or datetime.now(UTC))

    def to_dict(self, *, include_token: bool = False) -> dict[str, Any]:
        out = {
            "id": self.id,
            "session_id": str(self.session_id),
            "client_id": self.client_id,
            "claimed_at": self.claimed_at.isoformat(),
            "lease_until": self.lease_until.isoformat(),
            "status": self.status,
            "reason": self.reason,
            "released_at": self.released_at.isoformat() if self.released_at else None,
            "last_renewed_at": (
                self.last_renewed_at.isoformat() if self.last_renewed_at else None
            ),
            "active": self.is_active(),
        }
        if include_token:
            out["lease_token"] = self.lease_token
        return out


class WorkstreamCoordinator:
    """Daemon-owned leases for ongoing interactive workstreams."""

    def __init__(self, *, default_lease_seconds: int = 300) -> None:
        self._default_lease_seconds = max(1, int(default_lease_seconds))
        self._workstreams: dict[str, InteractiveWorkstream] = {}
        self._session_index: dict[UUID, str] = {}
        self._client_index: dict[str, set[str]] = defaultdict(set)
        self._lock = anyio.Lock()

    def _lease_seconds(self, lease_seconds: int | None = None) -> int:
        return max(1, int(lease_seconds or self._default_lease_seconds))

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _lease_expired(self, workstream: InteractiveWorkstream, now: datetime) -> bool:
        return workstream.status == "active" and workstream.lease_until <= now

    def _drop_indexes(self, workstream: InteractiveWorkstream) -> None:
        self._session_index.pop(workstream.session_id, None)
        self._client_index.get(workstream.client_id, set()).discard(workstream.id)
        if not self._client_index.get(workstream.client_id):
            self._client_index.pop(workstream.client_id, None)

    def _store(self, workstream: InteractiveWorkstream) -> InteractiveWorkstream:
        self._workstreams[workstream.id] = workstream
        self._session_index[workstream.session_id] = workstream.id
        self._client_index.setdefault(workstream.client_id, set()).add(workstream.id)
        return workstream

    def _retire(self, workstream: InteractiveWorkstream, *, status: str, now: datetime) -> InteractiveWorkstream:
        retired = InteractiveWorkstream(
            id=workstream.id,
            session_id=workstream.session_id,
            client_id=workstream.client_id,
            lease_token=workstream.lease_token,
            claimed_at=workstream.claimed_at,
            lease_until=workstream.lease_until,
            status=status,
            reason=workstream.reason,
            released_at=now if status != "active" else workstream.released_at,
            last_renewed_at=workstream.last_renewed_at,
        )
        self._workstreams[workstream.id] = retired
        self._drop_indexes(workstream)
        return retired

    def _current_for_session(self, session_id: UUID, *, now: datetime) -> InteractiveWorkstream | None:
        workstream_id = self._session_index.get(session_id)
        if workstream_id is None:
            return None
        workstream = self._workstreams.get(workstream_id)
        if workstream is None:
            self._session_index.pop(session_id, None)
            return None
        if self._lease_expired(workstream, now):
            self._retire(workstream, status="expired", now=now)
            return None
        if workstream.status != "active":
            self._drop_indexes(workstream)
            return None
        return workstream

    def _check_owner(
        self,
        workstream: InteractiveWorkstream,
        *,
        client_id: str,
        lease_token: str | None,
    ) -> None:
        if workstream.client_id == client_id:
            if lease_token is None or lease_token == workstream.lease_token:
                return
        raise WorkstreamOwnershipError(
            f"workstream {workstream.id} is owned by {workstream.client_id}",
        )

    async def claim(
        self,
        session_id: UUID,
        client_id: str,
        *,
        lease_seconds: int | None = None,
        lease_token: str | None = None,
        reason: str | None = None,
        workstream_id: str | None = None,
    ) -> InteractiveWorkstream:
        now = self._now()
        async with self._lock:
            current = self._current_for_session(session_id, now=now)
            if current is not None:
                if workstream_id is not None and workstream_id != current.id:
                    raise WorkstreamOwnershipError(
                        f"session {session_id} already has active workstream {current.id}",
                    )
                self._check_owner(current, client_id=client_id, lease_token=lease_token)
                updated = InteractiveWorkstream(
                    id=current.id,
                    session_id=current.session_id,
                    client_id=current.client_id,
                    lease_token=current.lease_token,
                    claimed_at=current.claimed_at,
                    lease_until=now + _lease_delta(self._lease_seconds(lease_seconds)),
                    status="active",
                    reason=reason if reason is not None else current.reason,
                    released_at=None,
                    last_renewed_at=now,
                )
                return self._store(updated)
            if workstream_id is not None and workstream_id in self._workstreams:
                existing = self._workstreams[workstream_id]
                if existing.status == "active" and existing.session_id != session_id:
                    raise WorkstreamOwnershipError(
                        f"workstream {workstream_id} is already bound to session {existing.session_id}",
                    )
            token = lease_token or secrets.token_urlsafe(32)
            workstream = InteractiveWorkstream(
                id=workstream_id or str(uuid4()),
                session_id=session_id,
                client_id=client_id,
                lease_token=token,
                claimed_at=now,
                lease_until=now + _lease_delta(self._lease_seconds(lease_seconds)),
                status="active",
                reason=reason,
                released_at=None,
                last_renewed_at=now,
            )
            return self._store(workstream)

    async def ensure(
        self,
        session_id: UUID,
        client_id: str,
        *,
        lease_seconds: int | None = None,
        lease_token: str | None = None,
        reason: str | None = None,
        auto_claim: bool = True,
    ) -> InteractiveWorkstream:
        now = self._now()
        async with self._lock:
            current = self._current_for_session(session_id, now=now)
            if current is not None:
                self._check_owner(current, client_id=client_id, lease_token=lease_token)
                updated = InteractiveWorkstream(
                    id=current.id,
                    session_id=current.session_id,
                    client_id=current.client_id,
                    lease_token=current.lease_token,
                    claimed_at=current.claimed_at,
                    lease_until=now + _lease_delta(self._lease_seconds(lease_seconds)),
                    status="active",
                    reason=reason if reason is not None else current.reason,
                    released_at=None,
                    last_renewed_at=now,
                )
                return self._store(updated)
            if not auto_claim:
                raise WorkstreamOwnershipError(f"session {session_id} has no active workstream")
            token = lease_token or secrets.token_urlsafe(32)
            workstream = InteractiveWorkstream(
                id=str(uuid4()),
                session_id=session_id,
                client_id=client_id,
                lease_token=token,
                claimed_at=now,
                lease_until=now + _lease_delta(self._lease_seconds(lease_seconds)),
                status="active",
                reason=reason,
                released_at=None,
                last_renewed_at=now,
            )
            return self._store(workstream)

    async def renew(
        self,
        workstream_id: str,
        *,
        client_id: str,
        lease_token: str | None = None,
        lease_seconds: int | None = None,
    ) -> InteractiveWorkstream:
        now = self._now()
        async with self._lock:
            workstream = self._workstreams.get(workstream_id)
            if workstream is None:
                raise WorkstreamOwnershipError(f"workstream {workstream_id} not found")
            if self._lease_expired(workstream, now):
                self._retire(workstream, status="expired", now=now)
                raise WorkstreamOwnershipError(f"workstream {workstream_id} has expired")
            self._check_owner(workstream, client_id=client_id, lease_token=lease_token)
            updated = InteractiveWorkstream(
                id=workstream.id,
                session_id=workstream.session_id,
                client_id=workstream.client_id,
                lease_token=workstream.lease_token,
                claimed_at=workstream.claimed_at,
                lease_until=now + _lease_delta(self._lease_seconds(lease_seconds)),
                status="active",
                reason=workstream.reason,
                released_at=None,
                last_renewed_at=now,
            )
            return self._store(updated)

    async def release(
        self,
        workstream_id: str,
        *,
        client_id: str,
        lease_token: str | None = None,
        reason: str | None = None,
    ) -> InteractiveWorkstream:
        now = self._now()
        async with self._lock:
            workstream = self._workstreams.get(workstream_id)
            if workstream is None:
                raise WorkstreamOwnershipError(f"workstream {workstream_id} not found")
            self._check_owner(workstream, client_id=client_id, lease_token=lease_token)
            retired = InteractiveWorkstream(
                id=workstream.id,
                session_id=workstream.session_id,
                client_id=workstream.client_id,
                lease_token=workstream.lease_token,
                claimed_at=workstream.claimed_at,
                lease_until=workstream.lease_until,
                status="released",
                reason=reason if reason is not None else workstream.reason,
                released_at=now,
                last_renewed_at=workstream.last_renewed_at,
            )
            self._workstreams[workstream.id] = retired
            self._drop_indexes(workstream)
            return retired

    async def release_session(self, session_id: UUID, *, reason: str | None = None) -> None:
        now = self._now()
        async with self._lock:
            current = self._current_for_session(session_id, now=now)
            if current is None:
                return
            self._retire(current, status="released", now=now)
            if reason is not None:
                retired = self._workstreams[current.id]
                self._workstreams[current.id] = InteractiveWorkstream(
                    id=retired.id,
                    session_id=retired.session_id,
                    client_id=retired.client_id,
                    lease_token=retired.lease_token,
                    claimed_at=retired.claimed_at,
                    lease_until=retired.lease_until,
                    status=retired.status,
                    reason=reason,
                    released_at=retired.released_at,
                    last_renewed_at=retired.last_renewed_at,
                )

    async def get(self, workstream_id: str) -> dict[str, Any] | None:
        async with self._lock:
            workstream = self._workstreams.get(workstream_id)
            if workstream is None:
                return None
            now = self._now()
            if self._lease_expired(workstream, now):
                self._retire(workstream, status="expired", now=now)
                workstream = self._workstreams.get(workstream_id)
            return workstream.to_dict(include_token=False) if workstream is not None else None

    async def list(
        self,
        *,
        session_id: UUID | None = None,
        client_id: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            now = self._now()
            items = list(self._workstreams.values())
            out: list[dict[str, Any]] = []
            for workstream in items:
                if self._lease_expired(workstream, now):
                    self._retire(workstream, status="expired", now=now)
                    workstream = self._workstreams.get(workstream.id)
                if workstream is None:
                    continue
                if session_id is not None and workstream.session_id != session_id:
                    continue
                if client_id is not None and workstream.client_id != client_id:
                    continue
                if active_only and not workstream.is_active(now=now):
                    continue
                out.append(workstream.to_dict(include_token=False))
            out.sort(key=lambda item: item["claimed_at"])
            return out

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            now = self._now()
            items = list(self._workstreams.values())
            active: list[dict[str, Any]] = []
            expired = 0
            released = 0
            by_client: dict[str, list[str]] = defaultdict(list)
            by_session: dict[str, dict[str, Any]] = {}
            for workstream in items:
                if self._lease_expired(workstream, now):
                    self._retire(workstream, status="expired", now=now)
                    workstream = self._workstreams.get(workstream.id)
                if workstream is None:
                    continue
                payload = workstream.to_dict(include_token=False)
                by_session[str(workstream.session_id)] = payload
                by_client[workstream.client_id].append(workstream.id)
                if workstream.status == "active":
                    active.append(payload)
                elif workstream.status == "expired":
                    expired += 1
                elif workstream.status == "released":
                    released += 1
            return {
                "count": len(self._workstreams),
                "active_count": len(active),
                "expired_count": expired,
                "released_count": released,
                "items": sorted(
                    (item for item in active),
                    key=lambda item: item["claimed_at"],
                ),
                "by_client": {client_id: sorted(ids) for client_id, ids in by_client.items()},
                "by_session": by_session,
            }

    async def owner_for_session(
        self,
        session_id: UUID,
    ) -> dict[str, Any] | None:
        async with self._lock:
            workstream = self._current_for_session(session_id, now=self._now())
            return workstream.to_dict(include_token=False) if workstream else None


def _lease_delta(seconds: int) -> timedelta:
    return timedelta(seconds=max(1, int(seconds)))


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

    def snapshot(self) -> dict[str, Any]:
        sessions = sorted(
            set(self._locks) | set(self._queues) | set(self._events),
            key=str,
        )
        return {
            "sessions": {
                str(session_id): {
                    "has_active_turn": self._locks.get(session_id).locked()
                    if session_id in self._locks
                    else False,
                    "pending_input_count": len(self._queues.get(session_id, ())),
                    "pending_inputs": [item.to_dict() for item in self._queues.get(session_id, ())],
                    "event_count": len(self._events.get(session_id, ())),
                    "last_event_type": (
                        self._events.get(session_id, ())[-1]["type"]
                        if self._events.get(session_id)
                        else None
                    ),
                }
                for session_id in sessions
            },
            "active_turn_count": sum(1 for lock in self._locks.values() if lock.locked()),
            "pending_input_count": sum(len(queue) for queue in self._queues.values()),
            "event_count": sum(len(events) for events in self._events.values()),
        }

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
