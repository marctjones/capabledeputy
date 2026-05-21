"""Sampling mediation port (spec 004 P1).

When an upstream MCP server requests sampling (delegating inference
back to the client), CapableDeputy routes the request through a
mediator port. The mediator:

  1. Receives the SamplingRequest (messages + maxTokens + optional
     model preferences)
  2. Applies chokepoint policy (does this session have an LLM
     capability? is the requesting MCP server authorized to sample?)
  3. Calls the configured LLM under the operator's risk preference
  4. Propagates labels (sampling result is labeled with whatever the
     upstream-server's inherent labels are PLUS any operator policy)
  5. Returns the response back to the requesting MCP server

The mediator port lets operators replace the default implementation
(routes to the daemon's LLMClient) with policy-aware variants:
  - SandboxedSamplingMediator — route via a separate quarantined LLM
  - QuotaLimitedMediator — enforce per-server inference budgets
  - LabeledSamplingMediator — apply per-server label policies
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SamplingRequest:
    """A sampling request originating from an upstream MCP server.

    Mirrors MCP's CreateMessageRequestParams shape (operator-facing,
    not bound to MCP type directly so the port stays independent).
    """

    messages: tuple[dict[str, Any], ...]
    max_tokens: int = 1000
    temperature: float | None = None
    system_prompt: str | None = None
    model_preferences: dict[str, Any] | None = None
    # Which upstream MCP server is asking (used for policy lookup +
    # label provenance).
    requesting_server: str = ""
    # The session within which this sampling fires (for chokepoint
    # context). May be None if the request is global.
    session_id: Any = None
    # Operator-supplied per-server inherent labels — applied to the
    # response so downstream consumers know the provenance.
    response_inherent_labels: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SamplingResponse:
    """Result of a mediated sampling call."""

    content: str
    model: str
    finish_reason: str
    # Labels attached to the response (operator-curated + provenance).
    applied_labels: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SamplingRefused:
    """The mediator refused the sampling request (policy, quota, etc.)."""

    reason: str
    rule: str = "sampling-refused"


class SamplingMediator(Protocol):
    """Sampling mediator contract.

    Implementations call into the daemon's LLM client (or a
    quarantined alternative) and may refuse based on policy.
    """

    name: str

    async def mediate(
        self,
        request: SamplingRequest,
    ) -> SamplingResponse | SamplingRefused:
        """Mediate a sampling request. Return SamplingResponse on
        success or SamplingRefused with a rule + reason."""
        ...
