"""In-memory session graph with fork/pause/resume operations (DESIGN.md §6).

Operations emit audit events through an injected AuditWriter when one
is provided. Persistence (SQLite) is layered separately in session.store
to keep the graph itself unit-testable without I/O.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.labels import Label
from capabledeputy.session.model import Session, SessionStatus
from capabledeputy.session.store import SessionStore


class SessionNotFoundError(KeyError):
    def __init__(self, session_id: UUID) -> None:
        super().__init__(f"session not found: {session_id}")
        self.session_id = session_id


class SessionStateError(RuntimeError):
    pass


class SessionGraph:
    def __init__(
        self,
        *,
        audit: AuditWriter | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self._sessions: dict[UUID, Session] = {}
        self._audit = audit
        self._store = store

    async def load(self) -> None:
        if self._store is None:
            return
        for s in await self._store.all():
            self._sessions[s.id] = s

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: UUID) -> bool:
        return session_id in self._sessions

    def get(self, session_id: UUID) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError as e:
            raise SessionNotFoundError(session_id) from e

    def list(self, status: SessionStatus | None = None) -> list[Session]:
        if status is None:
            return list(self._sessions.values())
        return [s for s in self._sessions.values() if s.status == status]

    def children(self, parent_id: UUID) -> list[Session]:
        return [s for s in self._sessions.values() if s.parent == parent_id]

    async def new(
        self,
        *,
        owner: str | None = None,
        intent: str | None = None,
    ) -> Session:
        session = Session.new(owner=owner, intent=intent)
        await self._save(session)
        self._sessions[session.id] = session
        await self._emit(
            EventType.SESSION_CREATED,
            session,
            owner=owner,
            intent=intent,
        )
        return session

    async def fork(
        self,
        parent_id: UUID,
        *,
        intent: str | None = None,
    ) -> Session:
        parent = self.get(parent_id)
        if parent.is_terminal:
            raise SessionStateError(
                f"cannot fork terminal session {parent_id} (status={parent.status})",
            )
        child = Session.new(
            parent=parent_id,
            owner=parent.owner,
            intent=intent,
            label_set=parent.label_set,
            capability_set=parent.capability_set,
            history=parent.history,
            declassification_log=parent.declassification_log,
        )
        await self._save(child)
        self._sessions[child.id] = child
        await self._emit(
            EventType.SESSION_FORKED,
            child,
            parent_id=str(parent_id),
            intent=intent,
        )
        return child

    async def pause(self, session_id: UUID) -> Session:
        return await self._transition(
            session_id,
            from_=SessionStatus.ACTIVE,
            to=SessionStatus.PAUSED,
            event=EventType.SESSION_PAUSED,
        )

    async def resume(self, session_id: UUID) -> Session:
        return await self._transition(
            session_id,
            from_=SessionStatus.PAUSED,
            to=SessionStatus.ACTIVE,
            event=EventType.SESSION_RESUMED,
        )

    async def add_labels(
        self,
        session_id: UUID,
        labels: frozenset[Label],
    ) -> Session:
        session = self.get(session_id)
        if not labels or labels.issubset(session.label_set):
            return session
        new_set = session.label_set | labels
        updated = replace(session, label_set=new_set, updated_at=datetime.now(UTC))
        await self._save(updated)
        self._sessions[session_id] = updated
        return updated

    async def abort(self, session_id: UUID) -> Session:
        session = self.get(session_id)
        if session.is_terminal:
            raise SessionStateError(
                f"session {session_id} already terminal (status={session.status})",
            )
        updated = session.with_status(SessionStatus.ABORTED)
        await self._save(updated)
        self._sessions[session_id] = updated
        await self._emit(EventType.SESSION_ABORTED, updated)
        return updated

    async def _transition(
        self,
        session_id: UUID,
        *,
        from_: SessionStatus,
        to: SessionStatus,
        event: EventType,
    ) -> Session:
        session = self.get(session_id)
        if session.status != from_:
            verb = event.value.split(".")[1]
            raise SessionStateError(
                f"cannot {verb} session in status {session.status}",
            )
        updated = session.with_status(to)
        await self._save(updated)
        self._sessions[session_id] = updated
        await self._emit(event, updated)
        return updated

    async def _save(self, session: Session) -> None:
        if self._store is not None:
            await self._store.upsert(session)

    def insert(self, session: Session) -> None:
        self._sessions[session.id] = session

    async def _emit(
        self,
        event_type: EventType,
        session: Session,
        **payload: object,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.write(
            Event(
                event_type=event_type,
                session_id=session.id,
                payload={k: v for k, v in payload.items() if v is not None},
            ),
        )
