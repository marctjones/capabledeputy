"""In-memory session graph with fork/pause/resume operations (DESIGN.md §6).

Operations emit audit events through an injected AuditWriter when one
is provided. Persistence (SQLite) is layered separately in session.store
to keep the graph itself unit-testable without I/O.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
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
from capabledeputy.policy.capabilities import (
    kind_name as _kind_name_of,
)
from capabledeputy.policy.envelope import RiskPreference
from capabledeputy.policy.labels import LabelState, most_restrictive_inherit
from capabledeputy.policy.precedence import resolve_risk_preference
from capabledeputy.policy.purposes import (
    UNSET_PURPOSE_HANDLE,
    Purposes,
    categories_of_capability,
)
from capabledeputy.provenance import ProvenanceRecorder, capability_node_id
from capabledeputy.session.model import (
    OriginMetadata,
    Session,
    SessionStatus,
    Turn,
    merge_session_artifacts,
)
from capabledeputy.session.store import SessionStore


def _resolve_spawn_dial(base: RiskPreference | None, purpose_dial: str) -> str:
    """#379 — resolve a session's spawn dial from the posture baseline and the
    purpose's dial. The posture BINDS the baseline; the purpose may only TIGHTEN
    it (never loosen). Without a posture baseline (`base is None`, legacy), the
    purpose's dial is used directly. Returns the string form the Session stores.

    Fail-safe: a purpose dial that doesn't parse (shouldn't happen — validated at
    load) falls back to the stricter baseline when one exists."""
    if base is None:
        return purpose_dial
    try:
        purpose_pref = RiskPreference(purpose_dial)
    except ValueError:
        return base.value
    return resolve_risk_preference(base, purpose_pref).value


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


class QuarantinedLLMUnavailableError(RuntimeError):
    """A Purpose declares recommended_pattern=pattern_2_dual_llm but
    no quarantined LLM is wired on the App. Principle VI fail-closed:
    we refuse the spawn rather than silently fall through to Pattern
    ① (which would expose the planner to adversarial content the
    operator declared inadmissible). The operator's fix is to either
    wire a quarantined LLM at daemon start or change the Purpose's
    recommended_pattern to ① + cope with the relaxed safety."""

    def __init__(self, *, purpose_handle: str) -> None:
        super().__init__(
            f"purpose {purpose_handle!r} recommends pattern_2_dual_llm "
            "but no quarantined LLM is wired on the App. Wire one at "
            "daemon start or change the Purpose's recommended_pattern.",
        )
        self.purpose_handle = purpose_handle


def _capability_descendant_ids(
    capabilities: frozenset[Capability],
    root_audit_id: UUID,
) -> frozenset[UUID]:
    """Return root + all current descendants in a single-parent cap tree."""
    present = {cap.audit_id for cap in capabilities}
    if root_audit_id not in present:
        return frozenset()
    by_parent: dict[UUID, list[Capability]] = {}
    for cap in capabilities:
        if cap.parent_audit_id is not None:
            by_parent.setdefault(cap.parent_audit_id, []).append(cap)
    removed: set[UUID] = set()
    stack = [root_audit_id]
    while stack:
        audit_id = stack.pop()
        if audit_id in removed:
            continue
        removed.add(audit_id)
        stack.extend(child.audit_id for child in by_parent.get(audit_id, ()))
    return frozenset(removed)


class SessionGraph:
    def __init__(
        self,
        *,
        audit: AuditWriter | None = None,
        store: SessionStore | None = None,
        purposes: Purposes | None = None,
        quarantined_available: bool = True,
        posture_risk_preference: RiskPreference | None = None,
    ) -> None:
        self._sessions: dict[UUID, Session] = {}
        self._audit = audit
        self._provenance = ProvenanceRecorder(audit)
        self._store = store
        self._purposes = purposes
        # #379 precedence lattice: the active posture's dial is the BASELINE a
        # purpose may only TIGHTEN, never loosen. None = no posture selected
        # (legacy: a purpose's dial is used directly).
        self._posture_risk_preference = posture_risk_preference
        # True when App.quarantined_llm is wired. Used by .new() to
        # fail-closed when a Purpose recommends Pattern ② DUAL_LLM but
        # no quarantined LLM is available — Principle VI requires we
        # refuse the spawn rather than silently fall through to
        # Pattern ① (which would expose the planner to adversarial
        # content the operator explicitly declared inadmissible).
        self._quarantined_available = quarantined_available

    def _has_restricted_candidates(self, categories: frozenset[str]) -> bool:
        """Heuristic for FR-047 spawn-refusal: if any candidate category
        is one of the operator-declared restricted-tier categories, the
        Pattern (3)/(5) requirement applies. Today we conservatively
        treat the literal substring 'restricted' or known restricted
        categories ('health', 'financial' at restricted tier) as the
        signal; a richer check that consults the loaded Purposes /
        labels.yaml category table is a follow-up."""
        restricted_hints = frozenset({"restricted", "health", "financial"})
        return any(c in restricted_hints for c in categories)

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
        restricted_tier_modes_available: tuple[bool, bool] | None = None,
        origin: OriginMetadata | dict[str, Any] | None = None,
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
        # T099 / FR-047 runtime activation — a session whose tool
        # surface has no Pattern (3) handle-aware tool AND no
        # SandboxActuator port is refused at spawn for restricted-
        # tier work. The caller passes
        # `restricted_tier_modes_available=(has_handles, has_sandbox)`
        # when it knows the surface; None disables the check (back-
        # compat). The check fires only when the caller indicates
        # restricted-tier intent via at least one RESTRICTED candidate
        # category — see _restricted_categories below.
        if restricted_tier_modes_available is not None and self._has_restricted_candidates(
            candidates,
        ):
            from capabledeputy.mode.dispatcher import ModeSelectionError, select_mode_for_restricted

            has_handles, has_sandbox = restricted_tier_modes_available
            try:
                select_mode_for_restricted(
                    has_accepts_handles_tool=has_handles,
                    has_sandbox_actuator=has_sandbox,
                )
            except ModeSelectionError as e:
                raise PurposeAdmissibilityError(
                    purpose_handle=purpose_handle,
                    inadmissible_categories=candidates,
                ) from e
        # Look up the purpose's default capabilities BEFORE creating
        # the session so the new Session is born with them. This
        # encodes the operator's standing intent: "every research
        # session has fs.read on ~/research/**" without forcing the
        # /grant flow per session. The purposes registry is fail-
        # closed: unknown or UNSET purpose contributes no defaults.
        default_caps: frozenset[Capability] = frozenset()
        # Issue 003 / Q1 (FR-030, 2026-05-25): the session's
        # risk_preference_at_spawn is resolved from its Purpose's
        # `risk_preference_dial` field. Different purposes carry
        # different defaults (tax-prep: cautious vs daily-briefing:
        # balanced). UNSET / unknown purpose falls back to the safety
        # default "cautious" — consistent with the Purpose dataclass
        # default + Constitution VI fail-closed.
        risk_preference_at_spawn = "cautious"
        if self._purposes is not None and purpose_handle != UNSET_PURPOSE_HANDLE:
            purpose = self._purposes.get(purpose_handle)
            if purpose is not None:
                if purpose.default_capabilities:
                    default_caps = frozenset(purpose.default_capabilities)
                # #379: a posture BINDS the baseline dial; the purpose may only
                # TIGHTEN it. Without an active posture (legacy), the purpose's
                # dial is used directly.
                risk_preference_at_spawn = _resolve_spawn_dial(
                    self._posture_risk_preference,
                    purpose.risk_preference_dial,
                )
                # Pattern ② precondition (Principle VI fail-closed): a
                # Purpose that recommends pattern_2_dual_llm requires
                # the quarantined LLM. Without it, the planner would
                # see raw adversarial content the operator declared
                # inadmissible. Refuse the spawn rather than silently
                # fall through to Pattern ①.
                if (
                    purpose.recommended_pattern == "pattern_2_dual_llm"
                    and not self._quarantined_available
                ):
                    raise QuarantinedLLMUnavailableError(
                        purpose_handle=purpose_handle,
                    )
        # Cookbook §4 #6 — cautious purposes get first-use prompts
        # automatically. The operator can still flip the flag later
        # via session.set_first_use_prompts. We only enable when the
        # operator EXPLICITLY picked a cautious purpose — the
        # fallback "cautious" dial for sessions without a purpose
        # is silent (preserves back-compat for /chat without
        # --persona). Non-cautious purposes leave the flag off so
        # balanced/aggressive sessions retain friction-free behavior.
        first_use_prompt_enabled = False
        if self._purposes is not None and purpose_handle != UNSET_PURPOSE_HANDLE:
            picked = self._purposes.get(purpose_handle)
            if picked is not None and picked.risk_preference_dial == "cautious":
                first_use_prompt_enabled = True
        session = Session.new(
            owner=owner,
            intent=intent,
            tool_aliasing=tool_aliasing,
            prefer_programmatic=prefer_programmatic,
            parent=parent,
            purpose_handle=purpose_handle,
            capability_set=default_caps,
            risk_preference_at_spawn=risk_preference_at_spawn,
            first_use_prompt_enabled=first_use_prompt_enabled,
            origin=origin,
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
            origin=session.origin.to_dict(),
        )
        # Audit the auto-granted caps individually so the trace shows
        # what the session is born with — same shape as explicit
        # /grant. Operators inspecting an audit log can see exactly
        # where each capability came from.
        for cap in default_caps:
            await self._emit(
                EventType.CAPABILITY_GRANTED,
                session,
                kind=_kind_name_of(cap.kind),
                pattern=cap.pattern,
                origin=cap.origin.value,
                source="purpose-default",
                purpose_handle=purpose_handle,
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
        # Q1 (FR-030, 2026-05-25): the child also inherits the
        # parent's resolved risk_preference_at_spawn. The dial is
        # bound to the Purpose at spawn time, not re-resolved on
        # fork — that way an operator who flipped the dial on the
        # parent's purpose mid-life doesn't accidentally apply the
        # new value to active child sessions (replayability per
        # SC-002).
        child = Session.new(
            parent=parent_id,
            owner=parent.owner,
            intent=intent,
            label_state=parent.label_state,
            axis_d=parent.axis_d,
            capability_set=parent.capability_set,
            history=parent.history,
            declassification_log=parent.declassification_log,
            purpose_handle=parent.purpose_handle,
            risk_preference_at_spawn=parent.risk_preference_at_spawn,
            origin=parent.origin,
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

    async def set_first_use_prompts(
        self,
        session_id: UUID,
        enabled: bool,
    ) -> Session:
        """Cookbook §4 #6 — flip the per-session first-action-of-kind
        prompt flag. Idempotent (no-op when already in the target
        state). No special audit event today: the next decide() that
        either fires or skips the FIRST_USE_OF_KIND_RULE is the
        evidence in the audit trail."""
        session = self.get(session_id)
        if session.first_use_prompt_enabled == enabled:
            return session
        updated = replace(
            session,
            first_use_prompt_enabled=enabled,
            updated_at=datetime.now(UTC),
        )
        await self._save(updated)
        self._sessions[session_id] = updated
        return updated

    async def set_enforcement_mode(
        self,
        session_id: UUID,
        mode: Any,
    ) -> Session:
        """Pattern ⑥ — flip the session's enforcement posture. The
        change is auditable (ENFORCEMENT_MODE_CHANGED event with
        old + new mode) so decisions before and after the flip can
        be unambiguously distinguished in a replay.

        No-op when the requested mode matches the current mode
        (still returns the session unchanged; no audit event)."""
        from capabledeputy.session.model import EnforcementMode

        if not isinstance(mode, EnforcementMode):
            mode = EnforcementMode(str(mode))
        session = self.get(session_id)
        if session.enforcement_mode == mode:
            return session
        old_mode = session.enforcement_mode
        updated = replace(
            session,
            enforcement_mode=mode,
            updated_at=datetime.now(UTC),
        )
        await self._save(updated)
        self._sessions[session_id] = updated
        await self._emit(
            EventType.ENFORCEMENT_MODE_CHANGED,
            updated,
            old_mode=old_mode.value,
            new_mode=mode.value,
        )
        return updated

    async def add_tags(
        self,
        session_id: UUID,
        delta: LabelState,
    ) -> Session:
        """Apply source #2 (§R5): raise a four-axis `LabelState` delta
        into the session via `most_restrictive_inherit` (monotone — only
        adds taint, never removes). The four-axis counterpart of
        `add_labels`; the dispatch chokepoint calls both so the session's
        `label_state` accumulates equivalently to the flat `label_set`
        until the flat leg is deleted (R4d/R7)."""
        if not delta.a and not delta.b:
            return self.get(session_id)
        session = self.get(session_id)
        composed = most_restrictive_inherit(session.label_state, delta)
        if composed == session.label_state:
            return session
        updated = replace(
            session,
            label_state=composed,
            updated_at=datetime.now(UTC),
        )
        await self._save(updated)
        self._sessions[session_id] = updated
        return updated

    async def record_used_kind(
        self,
        session_id: UUID,
        kind: CapabilityKind | str,
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

    async def add_session_artifacts(
        self,
        session_id: UUID,
        artifacts: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> Session:
        if not artifacts:
            return self.get(session_id)
        session = self.get(session_id)
        updated_handles = merge_session_artifacts(session.reference_handles, artifacts)
        updated = replace(
            session,
            reference_handles=updated_handles,
            updated_at=datetime.now(UTC),
        )
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
            # Issue #35 — capability.kind may be an enum OR a custom-kind
            # string. Both serialize to the bare name for audit events.
            _kind_str = (
                _kind_name_of(capability.kind)
                if hasattr(capability.kind, "value")
                else str(capability.kind)
            )
            await self._audit.write(
                Event(
                    event_type=EventType.CAPABILITY_GRANTED,
                    session_id=session_id,
                    payload={
                        "kind": _kind_str,
                        "pattern": capability.pattern,
                        "expiry": capability.expiry.value,
                        "origin": capability.origin.value,
                        "audit_id": str(capability.audit_id),
                    },
                ),
            )
        else:
            _kind_str = (
                _kind_name_of(capability.kind)
                if hasattr(capability.kind, "value")
                else str(capability.kind)
            )
        await self._provenance.node(
            session_id=session_id,
            node_id=capability_node_id(capability.audit_id),
            kind="capability",
            materialized_id=f"capability:{capability.audit_id}",
            metadata={
                "kind": _kind_str,
                "pattern": capability.pattern,
                "expiry": capability.expiry.value,
                "origin": capability.origin.value,
                "parent_audit_id": (
                    str(capability.parent_audit_id)
                    if capability.parent_audit_id is not None
                    else None
                ),
            },
        )
        return updated

    async def revoke_capability(
        self,
        session_id: UUID,
        capability_audit_id: UUID,
        *,
        trigger: str = "operator-revoke",
        eager_teardown: bool = False,
    ) -> Session:
        """Mark a capability revoked by adding its audit_id to the
        session's revoked_audit_ids set.

        By default the cascade is computed lazily at the next decide()
        — any descendant matching this ancestor at decision time fails
        with capability-cascaded. When ``eager_teardown`` is true, the
        revoked capability and all current descendant capabilities are
        also removed from the session immediately. The revoked id set is
        still retained so persisted or reintroduced descendants remain
        inert under the lazy check.

        Idempotent — re-revoking is a no-op (just returns the session).
        Operator/control-plane only; the AI cannot invoke this.

        Emits CAPABILITY_CASCADE_REVOKED audit event recording the
        originating audit_id and trigger for auditor reconstruction
        (FR-014 / SC-002).
        """
        session = self.get(session_id)
        if capability_audit_id in session.revoked_audit_ids:
            return session
        new_revoked = session.revoked_audit_ids | {capability_audit_id}
        removed_audit_ids: frozenset[UUID] = frozenset()
        new_caps = session.capability_set
        if eager_teardown:
            removed_audit_ids = _capability_descendant_ids(
                session.capability_set,
                capability_audit_id,
            )
            if removed_audit_ids:
                new_caps = frozenset(
                    cap for cap in session.capability_set if cap.audit_id not in removed_audit_ids
                )
        updated = replace(
            session,
            capability_set=new_caps,
            revoked_audit_ids=new_revoked,
            updated_at=datetime.now(UTC),
        )
        await self._save(updated)
        self._sessions[session_id] = updated
        if self._audit is not None:
            await self._audit.write(
                Event(
                    event_type=EventType.CAPABILITY_CASCADE_REVOKED,
                    session_id=session_id,
                    payload={
                        "audit_id": str(capability_audit_id),
                        "trigger": trigger,
                        "eager_teardown": eager_teardown,
                        "removed_audit_ids": sorted(str(aid) for aid in removed_audit_ids),
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
            kind=_kind_name_of(result.kind),
            depth=result.depth,
        )
        await self._provenance.edge(
            session_id=child_session_id,
            from_node_id=capability_node_id(live[0].audit_id),
            to_node_id=capability_node_id(result.audit_id),
            kind="delegated",
            metadata={
                "parent_session": str(parent_session_id),
                "child_session": str(child_session_id),
                "depth": result.depth,
            },
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
