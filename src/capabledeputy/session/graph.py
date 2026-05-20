"""In-memory session graph with fork/pause/resume operations (DESIGN.md §6).

Operations emit audit events through an injected AuditWriter when one
is provided. Persistence (SQLite) is layered separately in session.store
to keep the graph itself unit-testable without I/O.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    DelegationRefusal,
    DelegationRefusalReason,
    DelegationRequest,
    derive_delegated_capability,
)
from capabledeputy.policy.labels import Label
from capabledeputy.policy.purposes import (
    UNSET_PURPOSE_HANDLE,
    Purposes,
    categories_of_capability,
)
from capabledeputy.session.model import Session, SessionStatus, Turn
from capabledeputy.session.store import SessionStore


class SessionNotFoundError(KeyError):
    def __init__(self, session_id: UUID) -> None:
        super().__init__(f"session not found: {session_id}")
        self.session_id = session_id


class SessionStateError(RuntimeError):
    pass


class PurposeAdmissibilityError(RuntimeError):
    """T056/T057/T059 — A grant or spawn would introduce a category
    inadmissible for the session's declared purpose (FR-009/FR-046).
    Raised by SessionGraph operations; the caller is expected to
    convert into a structured refusal or audit event."""

    def __init__(
        self,
        *,
        purpose_handle: str,
        inadmissible_categories: frozenset[str],
    ) -> None:
        super().__init__(
            f"purpose {purpose_handle!r} does not admit categories "
            f"{sorted(inadmissible_categories)} (FR-009)",
        )
        self.purpose_handle = purpose_handle
        self.inadmissible_categories = inadmissible_categories


class SessionGraph:
    def __init__(
        self,
        *,
        audit: AuditWriter | None = None,
        store: SessionStore | None = None,
        purposes: Purposes | None = None,
    ) -> None:
        self._sessions: dict[UUID, Session] = {}
        self._audit = audit
        self._store = store
        self._purposes = purposes

    def _check_admissibility(
        self,
        *,
        purpose_handle: str,
        categories: frozenset[str],
    ) -> None:
        """Raise PurposeAdmissibilityError if any of `categories` is
        inadmissible for the given purpose. Fail-closed when no
        Purposes registry is configured AND categories were declared
        (operator has no way to admit anything — refuse). When
        categories is empty, no check needed (T056 only fires on
        declared categories)."""
        if not categories:
            return
        if self._purposes is None:
            # No registry ⇒ no purpose admits anything ⇒ everything
            # inadmissible. FR-046 fail-closed.
            raise PurposeAdmissibilityError(
                purpose_handle=purpose_handle,
                inadmissible_categories=categories,
            )
        inadmissible = categories_of_capability(
            cap_categories=categories,
            purposes=self._purposes,
            purpose_handle=purpose_handle,
        )
        if inadmissible:
            raise PurposeAdmissibilityError(
                purpose_handle=purpose_handle,
                inadmissible_categories=inadmissible,
            )

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
        tool_aliasing: bool = False,
        prefer_programmatic: bool = False,
        parent: UUID | None = None,
        purpose_handle: str = UNSET_PURPOSE_HANDLE,
        candidate_capability_categories: frozenset[str] | None = None,
    ) -> Session:
        """Spawn a new session.

        003 US3 (T056/T057): if `candidate_capability_categories` is
        supplied, every category must be admissible under
        `purpose_handle` — otherwise PurposeAdmissibilityError is
        raised before the session is created. Empty / None means
        no categories were declared at spawn; that's fine, but any
        subsequent grant_capability with categories will be checked.

        `purpose_handle` defaults to `unset` (FR-046 fail-closed:
        any subsequent declared-category grant is refused unless the
        caller explicitly picks a purpose).
        """
        candidates = candidate_capability_categories or frozenset()
        self._check_admissibility(
            purpose_handle=purpose_handle,
            categories=candidates,
        )
        session = Session.new(
            owner=owner,
            intent=intent,
            tool_aliasing=tool_aliasing,
            prefer_programmatic=prefer_programmatic,
            parent=parent,
            purpose_handle=purpose_handle,
        )
        await self._save(session)
        self._sessions[session.id] = session
        await self._emit(
            EventType.SESSION_CREATED,
            session,
            owner=owner,
            intent=intent,
            tool_aliasing=tool_aliasing or None,
            prefer_programmatic=prefer_programmatic or None,
            purpose_handle=(purpose_handle if purpose_handle != UNSET_PURPOSE_HANDLE else None),
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
        # T058 — purpose-preserving fork: the child inherits the
        # parent's purpose_handle so capabilities admissible in the
        # parent stay admissible in the child (and inadmissible ones
        # remain refused).
        child = Session.new(
            parent=parent_id,
            owner=parent.owner,
            intent=intent,
            label_set=parent.label_set,
            capability_set=parent.capability_set,
            history=parent.history,
            declassification_log=parent.declassification_log,
            purpose_handle=parent.purpose_handle,
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

    async def record_used_kind(
        self,
        session_id: UUID,
        kind: CapabilityKind,
    ) -> Session:
        session = self.get(session_id)
        if kind in session.used_kinds:
            return session
        new_used = session.used_kinds | {kind}
        updated = replace(session, used_kinds=new_used, updated_at=datetime.now(UTC))
        await self._save(updated)
        self._sessions[session_id] = updated
        return updated

    async def record_cap_use(
        self,
        session_id: UUID,
        audit_id: str,
        when: datetime,
        *,
        prune_older_than: timedelta | None = None,
    ) -> Session:
        """Append a use timestamp for a capability (keyed by audit_id)
        so the policy engine can enforce its sliding-window rate limit
        on the next decision. Optionally prune timestamps older than
        `prune_older_than` to bound growth (the caller passes the
        capability's window)."""
        session = self.get(session_id)
        prior = session.cap_uses.get(audit_id, ())
        stamps = (*prior, when)
        if prune_older_than is not None:
            cutoff = when - prune_older_than
            stamps = tuple(ts for ts in stamps if ts >= cutoff)
        new_uses = {**session.cap_uses, audit_id: stamps}
        updated = replace(
            session,
            cap_uses=new_uses,
            updated_at=datetime.now(UTC),
        )
        await self._save(updated)
        self._sessions[session_id] = updated
        return updated

    async def add_turn(self, session_id: UUID, turn: Turn) -> Session:
        session = self.get(session_id)
        new_history = (*session.history, turn)
        updated = replace(session, history=new_history, updated_at=datetime.now(UTC))
        await self._save(updated)
        self._sessions[session_id] = updated
        return updated

    async def grant_capability(
        self,
        session_id: UUID,
        capability: Capability,
        *,
        categories: frozenset[str] = frozenset(),
    ) -> Session:
        """Grant a capability to a session.

        003 US3 (T056 continued): if `categories` is non-empty, every
        category must be admissible under the session's
        `purpose_handle` — otherwise PurposeAdmissibilityError is
        raised before the grant lands. The caller (typically a tool
        wiring or an operator script) supplies the categories the
        capability's read scope spans; capabilities themselves do not
        yet carry that mapping (lands with T012 effect_class).
        """
        session = self.get(session_id)
        self._check_admissibility(
            purpose_handle=session.purpose_handle,
            categories=categories,
        )
        if capability in session.capability_set:
            return session
        new_caps = session.capability_set | {capability}
        updated = replace(session, capability_set=new_caps, updated_at=datetime.now(UTC))
        await self._save(updated)
        self._sessions[session_id] = updated
        if self._audit is not None:
            await self._audit.write(
                Event(
                    event_type=EventType.CAPABILITY_GRANTED,
                    session_id=session_id,
                    payload={
                        "kind": capability.kind.value,
                        "pattern": capability.pattern,
                        "expiry": capability.expiry.value,
                        "origin": capability.origin.value,
                        "audit_id": str(capability.audit_id),
                    },
                ),
            )
        return updated

    async def delegate(
        self,
        parent_session_id: UUID,
        child_session_id: UUID,
        request: DelegationRequest,
        *,
        depth_limit: int,
        now: datetime | None = None,
        categories: frozenset[str] = frozenset(),
    ) -> Capability | DelegationRefusal:
        """Delegate an attenuated capability from a parent session to a
        child it spawned (002 US1 / contracts C1, C3). The caller
        supplies only a narrowing `request`; the engine resolves the
        parent capability and derives the child — a model-supplied
        Capability is structurally impossible (FR-012). Session-context
        preconditions (kind-not-held, parent-inert, cycle/self) are
        enforced here; the per-dimension clamp is the pure
        `derive_delegated_capability`.
        """
        now = now or datetime.now(UTC)
        parent_session = self.get(parent_session_id)
        self.get(child_session_id)  # SessionNotFoundError if missing

        if parent_session_id == child_session_id:
            return await self._refuse_delegation(
                child_session_id,
                DelegationRefusalReason.SELF_DELEGATION,
            )

        # Acyclic: the child must not be an ancestor of the parent in
        # the spawn graph (would form a cycle in the authority graph).
        anc = parent_session.parent
        seen: set[UUID] = set()
        while anc is not None and anc not in seen:
            if anc == child_session_id:
                return await self._refuse_delegation(
                    child_session_id,
                    DelegationRefusalReason.CYCLE,
                )
            seen.add(anc)
            p = self._sessions.get(anc)
            anc = p.parent if p is not None else None

        candidates = sorted(
            (c for c in parent_session.capability_set if c.kind == request.kind),
            key=lambda c: str(c.audit_id),
        )
        if not candidates:
            return await self._refuse_delegation(
                child_session_id,
                DelegationRefusalReason.KIND_NOT_HELD,
            )
        live = [
            c
            for c in candidates
            if not c.is_expired(now) and c.audit_id not in parent_session.revoked_audit_ids
        ]
        if not live:
            return await self._refuse_delegation(
                child_session_id,
                DelegationRefusalReason.PARENT_DEAD,
            )

        result = derive_delegated_capability(
            live[0],
            request,
            depth_limit=depth_limit,
        )
        if isinstance(result, DelegationRefusal):
            return await self._refuse_delegation(child_session_id, result.reason)

        # T059 — purpose-admissibility check against the CHILD session's
        # purpose: a delegation that would introduce an inadmissible
        # category is refused, even if it would otherwise satisfy the
        # 002 attenuation rules.
        child_session = self.get(child_session_id)
        if categories:
            if self._purposes is None:
                return await self._refuse_delegation(
                    child_session_id,
                    DelegationRefusalReason.INADMISSIBLE_CATEGORY,
                )
            inadmissible = categories_of_capability(
                cap_categories=categories,
                purposes=self._purposes,
                purpose_handle=child_session.purpose_handle,
            )
            if inadmissible:
                return await self._refuse_delegation(
                    child_session_id,
                    DelegationRefusalReason.INADMISSIBLE_CATEGORY,
                )

        await self.grant_capability(child_session_id, result)
        await self._emit(
            EventType.DELEGATION_GRANTED,
            self.get(child_session_id),
            parent_session=str(parent_session_id),
            parent_audit_id=str(live[0].audit_id),
            child_audit_id=str(result.audit_id),
            kind=result.kind.value,
            depth=result.depth,
        )
        return result

    async def _refuse_delegation(
        self,
        child_session_id: UUID,
        reason: DelegationRefusalReason,
    ) -> DelegationRefusal:
        if self._audit is not None:
            await self._audit.write(
                Event(
                    event_type=EventType.DELEGATION_REFUSED,
                    session_id=child_session_id,
                    payload={"reason": reason.value},
                ),
            )
        return DelegationRefusal(reason)

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
