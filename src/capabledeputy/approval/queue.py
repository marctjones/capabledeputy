"""Approval queue: in-memory storage of pending and decided ApprovalRequests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from capabledeputy.approval.model import ApprovalAction, ApprovalRequest, ApprovalStatus
from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import Capability
from capabledeputy.policy.labels import Label


class ApprovalNotFoundError(KeyError):
    pass


class ApprovalStateError(RuntimeError):
    pass


class ApprovalQueue:
    def __init__(self, audit: AuditWriter | None = None) -> None:
        self._next_id = 1
        self._requests: dict[int, ApprovalRequest] = {}
        self._audit = audit

    def __len__(self) -> int:
        return len(self._requests)

    def get(self, request_id: int) -> ApprovalRequest:
        try:
            return self._requests[request_id]
        except KeyError as e:
            raise ApprovalNotFoundError(request_id) from e

    def list(
        self,
        status: ApprovalStatus | None = None,
    ) -> list[ApprovalRequest]:
        requests = list(self._requests.values())
        if status is not None:
            return [r for r in requests if r.status == status]
        return requests

    async def submit(
        self,
        *,
        from_session,
        action: ApprovalAction,
        payload: str,
        target: str,
        labels_in: frozenset[Label],
        labels_out: frozenset[Label] = frozenset(),
        capability_requested: Capability | None = None,
        justification: str = "",
    ) -> ApprovalRequest:
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
        )
        self._next_id += 1
        self._requests[request.id] = request
        if self._audit:
            await self._audit.write(
                Event(
                    event_type=EventType.APPROVAL_REQUESTED,
                    session_id=from_session,
                    payload={
                        "approval_id": request.id,
                        "action": action.value,
                        "target": target,
                        "labels_in": sorted(label.value for label in labels_in),
                        "justification": justification,
                    },
                ),
            )
        return request

    async def approve(
        self,
        request_id: int,
        *,
        decided_by: str = "user",
        decision_scope: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        request = self.get(request_id)
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalStateError(
                f"approval {request_id} not pending (status={request.status})",
            )
        updated = replace(
            request,
            status=ApprovalStatus.APPROVED,
            decision_at=datetime.now(UTC),
            decided_by=decided_by,
            decision_scope=decision_scope or {},
        )
        self._requests[request_id] = updated
        if self._audit:
            await self._audit.write(
                Event(
                    event_type=EventType.APPROVAL_APPROVED,
                    session_id=request.from_session,
                    payload={
                        "approval_id": request_id,
                        "decided_by": decided_by,
                        "decision_scope": decision_scope or {},
                    },
                ),
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
            await self._audit.write(
                Event(
                    event_type=EventType.APPROVAL_DENIED,
                    session_id=request.from_session,
                    payload={
                        "approval_id": request_id,
                        "decided_by": decided_by,
                        "reason": reason,
                    },
                ),
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
