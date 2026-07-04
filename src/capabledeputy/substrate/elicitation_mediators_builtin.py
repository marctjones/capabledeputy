"""Builtin ElicitationMediator implementations.

Reference mediators operators can register out of the box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from capabledeputy.substrate.elicitation_port import (
    ElicitationRefused,
    ElicitationRequest,
    ElicitationResponse,
)


@dataclass(frozen=True)
class RefuseAllElicitationMediator:
    """Strict mediator — refuses all elicitations.

    Default-safe posture: until the operator explicitly enables
    elicitation, no upstream server can prompt the user mid-flow.
    """

    name: str = "RefuseAllElicitationMediator"
    reason: str = "operator policy disables MCP elicitation"

    async def mediate(
        self,
        request: ElicitationRequest,
    ) -> ElicitationRefused:
        return ElicitationRefused(
            reason=self.reason,
            rule="elicitation-disabled-by-operator",
        )


@dataclass(frozen=True)
class AllowlistElicitationMediator:
    """Delegates to inner mediator only for an operator-declared
    allowlist of upstream MCP servers. Refuses others outright."""

    name: str = "AllowlistElicitationMediator"
    allowed_servers: frozenset[str] = field(default_factory=frozenset)
    inner: Any = None

    async def mediate(
        self,
        request: ElicitationRequest,
    ) -> ElicitationResponse | ElicitationRefused:
        if request.requesting_server not in self.allowed_servers:
            return ElicitationRefused(
                reason=(
                    f"server {request.requesting_server!r} is not on the elicitation allowlist"
                ),
                rule="elicitation-server-not-allowed",
            )
        if self.inner is None:
            return ElicitationRefused(
                reason="allowlist passed but no inner mediator configured",
                rule="elicitation-no-inner",
            )
        return await self.inner.mediate(request)


@dataclass
class ApprovalQueueElicitationMediator:
    """Route elicitations to the approval queue so they appear in
    the same operator UI as policy-gated approvals.

    The approval queue treats elicitations as a special-flag approval
    request — the operator sees the prompt and the requesting server,
    types a response into the same console workflow that handles
    chokepoint-gated tool calls.

    Configure with the daemon's approval_queue at construction.
    """

    name: str = "ApprovalQueueElicitationMediator"
    approval_queue: Any = None
    timeout_seconds: float = 300.0

    async def mediate(
        self,
        request: ElicitationRequest,
    ) -> ElicitationResponse | ElicitationRefused:
        if self.approval_queue is None:
            return ElicitationRefused(
                reason="no approval queue configured for elicitation routing",
                rule="elicitation-no-queue",
            )
        if request.session_id is None:
            return ElicitationRefused(
                reason="approval-queue elicitation routing requires a session_id",
                rule="elicitation-no-session",
            )
        try:
            session_id = (
                request.session_id
                if isinstance(request.session_id, UUID)
                else UUID(str(request.session_id))
            )
            queued = await self.approval_queue.submit_elicitation(
                from_session=session_id,
                prompt=request.prompt,
                requesting_server=request.requesting_server,
                schema=request.schema,
                response_inherent_labels=request.response_inherent_labels,
            )
            response_value = await self.approval_queue.wait_for_elicitation_response(
                queued.id,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            return ElicitationRefused(
                reason=str(exc),
                rule="elicitation-approval-queue-failed",
            )
        return ElicitationResponse(
            response_value=response_value,
            decided_by="approval-queue",
            applied_labels=request.response_inherent_labels,
        )
