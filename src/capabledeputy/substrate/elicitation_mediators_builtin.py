"""Builtin ElicitationMediator implementations.

Reference mediators operators can register out of the box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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

    async def mediate(
        self,
        request: ElicitationRequest,
    ) -> ElicitationResponse | ElicitationRefused:
        if self.approval_queue is None:
            return ElicitationRefused(
                reason="no approval queue configured for elicitation routing",
                rule="elicitation-no-queue",
            )
        # The actual queue wire-up is operator-side; here we just
        # surface a structural failure if it isn't connected. A
        # follow-up commit will implement queue.submit_elicitation()
        # so this mediator can actually post + wait for the user's
        # response. For now, this is the port that lets the surface
        # be in place.
        return ElicitationRefused(
            reason=(
                "approval-queue routing of elicitations not yet implemented; "
                "configure RefuseAllElicitationMediator until follow-up lands"
            ),
            rule="elicitation-routing-not-implemented",
        )
