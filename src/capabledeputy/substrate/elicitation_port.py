"""Elicitation port (spec 004 P1).

MCP's elicitation surface lets an upstream server prompt the user
for additional input mid-flow. ("I need a confirmation code"; "Pick
a destination folder"; "Approve this payment.")

CapableDeputy routes elicitations through an ElicitationMediator
under chokepoint policy. The mediator can:
  - Route the elicitation to the approval queue (so it appears in
    the same UI as policy-gated approvals)
  - Refuse outright (operator disabled elicitation)
  - Apply per-server policy (allowlist, rate limit, etc.)

This keeps the operator's attention surface unified — every
mid-flow prompt the agent surfaces, whether from a chokepoint
deny-into-approval transition or an upstream MCP server's
elicitation, flows through one queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ElicitationRequest:
    """A mid-flow input request from an upstream MCP server.

    Mirrors MCP's elicitation/create shape but stays independent of
    the MCP type so the port is portable.
    """

    prompt: str
    schema: dict[str, Any] | None = None
    requesting_server: str = ""
    session_id: Any = None
    # Operator-supplied labels for the response — propagate when the
    # user's reply flows back into the session.
    response_inherent_labels: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ElicitationResponse:
    """Operator/user response to an elicitation."""

    response_value: dict[str, Any]
    decided_by: str = "user"
    applied_labels: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ElicitationRefused:
    """Mediator refused the elicitation request."""

    reason: str
    rule: str = "elicitation-refused"


class ElicitationMediator(Protocol):
    """Mediator contract for elicitation requests."""

    name: str

    async def mediate(
        self,
        request: ElicitationRequest,
    ) -> ElicitationResponse | ElicitationRefused:
        """Process an elicitation request. Return ElicitationResponse
        with the user's reply or ElicitationRefused with policy reason."""
        ...
