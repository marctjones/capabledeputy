"""Approval queue: in-memory storage of pending and decided ApprovalRequests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from capabledeputy.approval.model import ApprovalAction, ApprovalRequest, ApprovalStatus
from capabledeputy.approval.pattern import ApprovalPatternRegistry
from capabledeputy.approval.signer import (
    ApprovalSigner,
    Signature,
    SignerError,
    canonical_payload,
)
from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import Capability
from capabledeputy.policy.labels import LabelState
from capabledeputy.provenance import (
    ProvenanceRecorder,
    approval_decision_node_id,
    approval_request_node_id,
    capability_node_id,
)

# Cookbook P2.1 — window during which two requests with the same
# (session, action, target) are considered siblings. 5 seconds is
# tuned for typical agent burst behavior (sequential send/draft
# fires within ~1 sec, occasional plan-then-act bursts within 2-3).
# Outside this window we treat the requests as independent decisions.
SIBLING_GROUPING_WINDOW = timedelta(seconds=5)

# Cookbook P2.7 — default approval TTL. After this many seconds in
# the PENDING state, the queue auto-flips the request to EXPIRED and
# emits APPROVAL_EXPIRED so the agent gets a typed signal (instead
# of waiting on a 3-day-old card forever). 300 seconds (5 min) is
# the default for operator attention windows; can be overridden per
# ApprovalQueue construction (constructor arg) or per request
# (submit's ttl_seconds kwarg). ttl_seconds=0 ⇒ no expiry.
DEFAULT_APPROVAL_TTL_SECONDS = 300


class ApprovalNotFoundError(KeyError):
    pass


class ApprovalStateError(RuntimeError):
    pass


class ApprovalSignatureRequiredError(ApprovalStateError):
    pass


class ApprovalQueue:
    def __init__(
        self,
        audit: AuditWriter | None = None,
        pattern_registry: ApprovalPatternRegistry | None = None,
        graph: Any = None,
        default_ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
    ) -> None:
        self._next_id = 1
        self._requests: dict[int, ApprovalRequest] = {}
        self._audit = audit
        self._provenance = ProvenanceRecorder(audit)
        self._graph = graph
        self.patterns = pattern_registry or ApprovalPatternRegistry()
        # Cookbook P2.7 — default TTL applied to new submissions when
        # the caller doesn't pass an explicit ttl_seconds. 0 ⇒
        # no auto-expiry (legacy / explicit immortal behavior).
        self._default_ttl_seconds = max(0, default_ttl_seconds)

    def __len__(self) -> int:
        return len(self._requests)

    def get(self, request_id: int) -> ApprovalRequest:
        try:
            request = self._requests[request_id]
        except KeyError as e:
            raise ApprovalNotFoundError(request_id) from e
        # Cookbook P2.7 — opportunistic stale-approval expiry. Every
        # queue access checks the requested entry's TTL and flips
        # PENDING → EXPIRED if it elapsed. Cheap enough to do in-
        # line; avoids a background sweeper task.
        return self._maybe_expire_sync(request)

    def list(
        self,
        status: ApprovalStatus | None = None,
    ) -> list[ApprovalRequest]:
        # Sweep stale entries before answering — list() is the
        # primary surface for the chat REPL `/approvals` and the
        # `capdep approval` CLI, so expiry needs to surface here.
        # Each PENDING entry past its TTL is flipped + emits
        # APPROVAL_EXPIRED. We schedule the audit writes on the
        # event loop so list() can stay sync (most callers don't
        # await).
        self._sweep_expired_sync()
        requests = list(self._requests.values())
        if status is not None:
            return [r for r in requests if r.status == status]
        return requests

    def siblings(self, group_id: UUID) -> list[ApprovalRequest]:
        """Every request belonging to a sibling group. Includes both
        pending and decided members so the audit UI can show the
        full grouping history. Sorted by id for stable display."""
        self._sweep_expired_sync()
        return sorted(
            (r for r in self._requests.values() if r.sibling_group_id == group_id),
            key=lambda r: r.id,
        )

    # --- TTL expiry helpers (cookbook P2.7) ------------------------------

    def _maybe_expire_sync(
        self,
        request: ApprovalRequest,
        *,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        """If `request` is PENDING and past its expires_at, flip it
        to EXPIRED in place and schedule an APPROVAL_EXPIRED audit.
        Returns the (possibly updated) request. No-op on non-pending
        or no-TTL requests."""
        if request.status != ApprovalStatus.PENDING:
            return request
        if request.expires_at is None:
            return request
        now = now or datetime.now(UTC)
        if now < request.expires_at:
            return request
        expired = replace(
            request,
            status=ApprovalStatus.EXPIRED,
            decision_at=now,
            decided_by="ttl",
        )
        self._requests[expired.id] = expired
        self._schedule_expired_audit(expired)
        return expired

    def _sweep_expired_sync(self, *, now: datetime | None = None) -> None:
        """Walk every PENDING entry once and call _maybe_expire_sync.
        Linear in queue size; the queue is small in practice (active
        approvals at any one time are sparse)."""
        now = now or datetime.now(UTC)
        for r in list(self._requests.values()):
            if r.status == ApprovalStatus.PENDING:
                self._maybe_expire_sync(r, now=now)

    def _schedule_expired_audit(self, request: ApprovalRequest) -> None:
        """Schedule the APPROVAL_EXPIRED audit write without blocking
        the sync call path. When there's no running event loop
        (tests calling list() outside an async context), the audit
        is dropped silently — the queue state itself is the
        durable record. When there IS a loop, the write is queued
        via asyncio.create_task and runs concurrently."""
        if self._audit is None:
            return
        import asyncio
        import contextlib

        async def _write() -> None:
            if self._audit is None:
                return
            await self._audit.write(
                Event(
                    event_type=EventType.APPROVAL_EXPIRED,
                    session_id=request.from_session,
                    payload={
                        "approval_id": request.id,
                        "action": request.action.value,
                        "target": request.target,
                        "expired_at": (
                            request.decision_at.isoformat() if request.decision_at else None
                        ),
                    },
                ),
            )

        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(_write())

    def _find_sibling_group(
        self,
        *,
        from_session,
        action: ApprovalAction,
        target: str,
    ) -> UUID | None:
        """Look for a pending request within the grouping window that
        shares (session, action, target) with the incoming request.

        Two cases:
          - matching request already has a sibling_group_id → reuse it
            (the third sibling joins an existing group).
          - matching request has no group yet → mint a fresh group_id,
            stamp it on the prior request via `replace`, return it.

        Returns None when no candidate sibling exists; the new request
        stands alone.
        """
        now = datetime.now(UTC)
        cutoff = now - SIBLING_GROUPING_WINDOW
        candidates = [
            r
            for r in self._requests.values()
            if r.status == ApprovalStatus.PENDING
            and r.from_session == from_session
            and r.action == action
            and r.target == target
            and r.requested_at >= cutoff
        ]
        if not candidates:
            return None
        # Use the most recent matching pending request as the anchor.
        anchor = max(candidates, key=lambda r: r.requested_at)
        if anchor.sibling_group_id is not None:
            return anchor.sibling_group_id
        new_group_id = uuid4()
        self._requests[anchor.id] = replace(
            anchor,
            sibling_group_id=new_group_id,
        )
        return new_group_id

    async def approve_group(
        self,
        group_id: UUID,
        *,
        decided_by: str = "user",
        decision_scope: dict[str, Any] | None = None,
    ) -> list[ApprovalRequest]:
        """Approve every PENDING member of `group_id`. Already-decided
        members are skipped. Returns the list of newly-approved
        requests in id order. The UI's `approve-all` button calls
        this; per-item rejection uses the standard `deny(id)` on each
        skipped member before calling this."""
        members = self.siblings(group_id)
        approved: list[ApprovalRequest] = []
        for m in members:
            if m.status == ApprovalStatus.PENDING:
                approved.append(
                    await self.approve(
                        m.id,
                        decided_by=decided_by,
                        decision_scope=decision_scope,
                    ),
                )
        return approved

    async def submit(
        self,
        *,
        from_session,
        action: ApprovalAction,
        payload: str,
        target: str,
        labels_in: LabelState | None = None,
        labels_out: LabelState | None = None,
        capability_requested: Capability | None = None,
        justification: str = "",
        ttl_seconds: int | None = None,
        rule: str | None = None,
    ) -> ApprovalRequest:
        # Default-tolerant on labels
        if labels_in is None:
            labels_in = LabelState()
        if labels_out is None:
            labels_out = LabelState()
        sibling_group_id = self._find_sibling_group(
            from_session=from_session,
            action=action,
            target=target,
        )
        # Cookbook P2.7 — resolve TTL: explicit caller > queue
        # default. ttl_seconds=0 ⇒ immortal request (None on the
        # field); otherwise expires_at = now + ttl.
        effective_ttl = self._default_ttl_seconds if ttl_seconds is None else max(0, ttl_seconds)
        expires_at: datetime | None = None
        if effective_ttl > 0:
            expires_at = datetime.now(UTC) + timedelta(seconds=effective_ttl)
        request = ApprovalRequest(
            id=self._next_id,
            audit_id=uuid4(),
            from_session=from_session,
            action=action,
            payload=payload,
            target=target,
            labels_in=labels_in,
            labels_out=labels_out,
            capability_requested=capability_requested,
            justification=justification,
            sibling_group_id=sibling_group_id,
            expires_at=expires_at,
            rule=rule,
        )
        self._next_id += 1
        self._requests[request.id] = request
        if self._audit:
            event = Event(
                event_type=EventType.APPROVAL_REQUESTED,
                session_id=from_session,
                payload={
                    "approval_id": request.id,
                    "action": action.value,
                    "target": target,
                    "labels_in": labels_in.to_dict(),
                    "justification": justification,
                },
            )
            await self._audit.write(event)
            request_node_id = approval_request_node_id(request.id)
            await self._provenance.node(
                session_id=from_session,
                node_id=request_node_id,
                kind="approval_request",
                materialized_id=f"approval:{request.id}",
                label_state=labels_in,
                event_audit_id=event.audit_id,
                metadata={
                    "action": action.value,
                    "target": target,
                    "rule": rule,
                },
            )
            if capability_requested is not None:
                await self._provenance.edge(
                    session_id=from_session,
                    from_node_id=capability_node_id(capability_requested.audit_id),
                    to_node_id=request_node_id,
                    kind="approval_authority",
                    event_audit_id=event.audit_id,
                )
        matched = self.patterns.find_match(request)
        if matched is not None:
            self.patterns.increment_use(matched.id)
            await self.approve(
                request.id,
                decided_by=f"pattern:{matched.id}",
                decision_scope={"matched_rule": str(matched.id)},
            )
            return self._requests[request.id]
        return request

    async def submit_elicitation(
        self,
        *,
        from_session,
        prompt: str,
        requesting_server: str = "",
        schema: dict[str, Any] | None = None,
        response_inherent_labels: frozenset[str] = frozenset(),
        ttl_seconds: int | None = None,
    ) -> ApprovalRequest:
        """Queue an MCP elicitation as an operator-visible approval request.

        The approval decision must include ``decision_scope["elicitation_response"]``
        before a mediator can return a user response to the upstream server.
        This keeps approval and arbitrary user input distinct: clicking approve
        is not silently treated as a fabricated answer.
        """

        payload = json.dumps(
            {
                "prompt": prompt,
                "schema": schema or {},
                "requesting_server": requesting_server,
                "response_inherent_labels": sorted(response_inherent_labels),
            },
            sort_keys=True,
        )
        target = requesting_server or "upstream-mcp"
        return await self.submit(
            from_session=from_session,
            action=ApprovalAction.ELICITATION,
            payload=payload,
            target=target,
            justification=f"MCP elicitation from {target}: {prompt}",
            ttl_seconds=ttl_seconds,
            rule="mcp-elicitation",
        )

    async def complete_elicitation(
        self,
        request_id: int,
        *,
        response_value: dict[str, Any],
        decided_by: str = "user",
    ) -> ApprovalRequest:
        """Approve an elicitation with the operator's structured response."""

        return await self.approve(
            request_id,
            decided_by=decided_by,
            decision_scope={"elicitation_response": response_value},
        )

    async def wait_for_elicitation_response(
        self,
        request_id: int,
        *,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 0.1,
    ) -> dict[str, Any]:
        """Wait until an elicitation request is completed, denied, or expires."""

        deadline = (
            None
            if timeout_seconds is None
            else datetime.now(UTC)
            + timedelta(
                seconds=timeout_seconds,
            )
        )
        while True:
            request = self.get(request_id)
            if request.status == ApprovalStatus.APPROVED:
                response = request.decision_scope.get("elicitation_response")
                if not isinstance(response, dict):
                    raise ApprovalStateError(
                        f"elicitation {request_id} approved without elicitation_response",
                    )
                return response
            if request.status in {
                ApprovalStatus.DENIED,
                ApprovalStatus.DEFERRED,
                ApprovalStatus.EXPIRED,
            }:
                reason = request.decision_scope.get("reason") or request.status.value
                raise ApprovalStateError(f"elicitation {request_id} not completed: {reason}")
            if deadline is not None and datetime.now(UTC) >= deadline:
                raise ApprovalStateError(f"elicitation {request_id} timed out")
            await asyncio.sleep(poll_interval_seconds)

    async def approve(
        self,
        request_id: int,
        *,
        decided_by: str = "user",
        decision_scope: dict[str, Any] | None = None,
        signature: Signature | None = None,
        signer_for_verify: ApprovalSigner | None = None,
        require_signature: bool = False,
    ) -> ApprovalRequest:
        """Approve a pending request.

        When `require_signature=True`, the caller must pass both a
        `signature` over the canonical payload and a `signer_for_verify`
        that can validate it. A missing or invalid signature blocks the
        approval and surfaces a clear error; the request stays PENDING
        so it can be retried with a valid signature.
        """
        request = self.get(request_id)
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalStateError(
                f"approval {request_id} not pending (status={request.status})",
            )

        # 002 US2 / FR-014 — refuse to approve into ALLOW if the
        # capability that would authorize the action is now inert
        # (revoked / expired / cascaded). The chokepoint would deny
        # it anyway at execution; refusing here is the audited form.
        if request.capability_requested is not None:
            cap = request.capability_requested
            from capabledeputy.policy.engine import (
                _build_audit_id_index,
                _is_cascaded_inert,
            )

            from_session_id = request.from_session
            session_caps: frozenset = frozenset()
            session_revoked: frozenset = frozenset()
            if from_session_id is not None:
                try:
                    s = self._graph.get(from_session_id) if self._graph else None
                except Exception:
                    s = None
                if s is not None:
                    session_caps = s.capability_set
                    session_revoked = getattr(s, "revoked_audit_ids", frozenset())
            now = datetime.now(UTC)
            # Consult the session's caps to walk the chain.
            cap_index = _build_audit_id_index(session_caps | {cap})
            cascaded, originator = _is_cascaded_inert(
                cap,
                cap_index=cap_index,
                revoked_audit_ids=session_revoked,
                now=now,
                cap_uses=None,
            )
            if cascaded:
                # Mark the approval invalidated rather than approving it.
                originator_id = originator.audit_id if originator is not None else cap.audit_id
                invalidated = replace(
                    request,
                    status=ApprovalStatus.DENIED,
                    decision_at=now,
                    decided_by="cascade-invalidation",
                    decision_scope={
                        "reason": "capability-cascaded",
                        "originating_audit_id": str(originator_id),
                    },
                )
                self._requests[request_id] = invalidated
                if self._audit:
                    await self._audit.write(
                        Event(
                            event_type=EventType.CAPABILITY_CASCADE_REVOKED,
                            session_id=from_session_id,
                            payload={
                                "approval_id": request_id,
                                "originating_audit_id": str(originator_id),
                                "trigger": "approval-revisit",
                                "affected_audit_id": str(cap.audit_id),
                            },
                        ),
                    )
                raise ApprovalStateError(
                    f"approval {request_id} invalidated: capability "
                    f"{cap.audit_id} is cascaded-inert (originator "
                    f"{originator_id})",
                )

        if require_signature:
            if signature is None or signer_for_verify is None:
                raise ApprovalSignatureRequiredError(
                    f"approval {request_id} requires a signature",
                )
            # Convert LabelState to a set of label string values for canonical_payload
            request_label_values = set()
            # Axis A: category labels
            for cat_tag in request.labels_in.a:
                request_label_values.add(f"confidential.{cat_tag.category}")
            # Axis B: provenance labels
            for prov_tag in request.labels_in.b:
                if prov_tag.level.value == "external-untrusted":
                    request_label_values.add("untrusted.external")
                elif prov_tag.level.value == "principal-direct":
                    request_label_values.add("trusted.user_direct")

            message = canonical_payload(
                approval_id=request.id,
                action=request.action.value,
                target=request.target,
                payload=request.payload,
                labels_in=frozenset(request_label_values),
            )
            try:
                ok = signer_for_verify.verify(message, signature)
            except SignerError as e:
                raise ApprovalSignatureRequiredError(
                    f"signature verification failed for approval {request_id}: {e}",
                ) from e
            if not ok:
                raise ApprovalSignatureRequiredError(
                    f"signature did not validate for approval {request_id}",
                )

        scope = dict(decision_scope or {})
        if signature is not None:
            scope["signature"] = signature.to_dict()

        updated = replace(
            request,
            status=ApprovalStatus.APPROVED,
            decision_at=datetime.now(UTC),
            decided_by=decided_by,
            decision_scope=scope,
        )
        self._requests[request_id] = updated
        if self._audit:
            event = Event(
                event_type=EventType.APPROVAL_APPROVED,
                session_id=request.from_session,
                payload={
                    "approval_id": request_id,
                    "decided_by": decided_by,
                    "decision_scope": scope,
                },
            )
            await self._audit.write(event)
            decision_node_id = approval_decision_node_id(event.audit_id)
            await self._provenance.node(
                session_id=request.from_session,
                node_id=decision_node_id,
                kind="approval_decision",
                materialized_id=f"approval:{request_id}:approved",
                event_audit_id=event.audit_id,
                metadata={
                    "approval_id": request_id,
                    "status": "approved",
                    "decided_by": decided_by,
                },
            )
            await self._provenance.edge(
                session_id=request.from_session,
                from_node_id=approval_request_node_id(request_id),
                to_node_id=decision_node_id,
                kind="decided",
                event_audit_id=event.audit_id,
            )
        return updated

    async def deny(
        self,
        request_id: int,
        *,
        decided_by: str = "user",
        reason: str = "",
    ) -> ApprovalRequest:
        request = self.get(request_id)
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalStateError(
                f"approval {request_id} not pending (status={request.status})",
            )
        updated = replace(
            request,
            status=ApprovalStatus.DENIED,
            decision_at=datetime.now(UTC),
            decided_by=decided_by,
            decision_scope={"reason": reason} if reason else {},
        )
        self._requests[request_id] = updated
        if self._audit:
            event = Event(
                event_type=EventType.APPROVAL_DENIED,
                session_id=request.from_session,
                payload={
                    "approval_id": request_id,
                    "decided_by": decided_by,
                    "reason": reason,
                },
            )
            await self._audit.write(event)
            decision_node_id = approval_decision_node_id(event.audit_id)
            await self._provenance.node(
                session_id=request.from_session,
                node_id=decision_node_id,
                kind="approval_decision",
                materialized_id=f"approval:{request_id}:denied",
                event_audit_id=event.audit_id,
                metadata={
                    "approval_id": request_id,
                    "status": "denied",
                    "decided_by": decided_by,
                    "reason": reason,
                },
            )
            await self._provenance.edge(
                session_id=request.from_session,
                from_node_id=approval_request_node_id(request_id),
                to_node_id=decision_node_id,
                kind="decided",
                event_audit_id=event.audit_id,
            )
        return updated

    async def defer(self, request_id: int) -> ApprovalRequest:
        request = self.get(request_id)
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalStateError(
                f"approval {request_id} not pending (status={request.status})",
            )
        updated = replace(request, status=ApprovalStatus.DEFERRED)
        self._requests[request_id] = updated
        if self._audit:
            await self._audit.write(
                Event(
                    event_type=EventType.APPROVAL_DEFERRED,
                    session_id=request.from_session,
                    payload={"approval_id": request_id},
                ),
            )
        return updated
