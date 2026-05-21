"""Builtin SamplingMediator implementations.

Reference mediators operators can register out of the box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from capabledeputy.substrate.sampling_port import (
    SamplingRefused,
    SamplingRequest,
    SamplingResponse,
)


@dataclass
class LiteLLMSamplingMediator:
    """Routes sampling requests to the daemon's main LLM client.

    Simplest mediator — every sampling request goes to the same LLM
    the agent uses. The operator may prefer a separate quarantined
    LLM (see QuarantinedSamplingMediator) for samplings from untrusted
    upstream servers.
    """

    name: str = "LiteLLMSamplingMediator"
    llm_client: Any = None  # set at construction; LLMClient instance

    async def mediate(
        self,
        request: SamplingRequest,
    ) -> SamplingResponse | SamplingRefused:
        if self.llm_client is None:
            return SamplingRefused(
                reason="no LLM client configured for sampling mediation",
                rule="sampling-no-llm",
            )
        # Translate the sampling request into the LLM client's shape
        from capabledeputy.llm.types import Message, Role

        messages: list[Message] = []
        if request.system_prompt:
            messages.append(Message(role=Role.SYSTEM, content=request.system_prompt))
        for m in request.messages:
            role_str = str(m.get("role", "user"))
            try:
                role = Role(role_str)
            except ValueError:
                role = Role.USER
            messages.append(Message(role=role, content=str(m.get("content", ""))))
        # No tools exposed for sampling (the upstream MCP server is
        # requesting raw inference, not a tool-using agent loop).
        response = await self.llm_client.respond(messages, [])
        return SamplingResponse(
            content=response.content or "",
            model=getattr(response, "model", "unknown"),
            finish_reason=response.finish_reason.value
            if hasattr(response.finish_reason, "value")
            else str(response.finish_reason),
            applied_labels=request.response_inherent_labels,
        )


@dataclass(frozen=True)
class RefuseAllSamplingMediator:
    """Strict mediator — refuses all sampling requests.

    Use when sampling is undesired across the board (e.g., the operator
    doesn't want upstream servers consuming inference budget).
    """

    name: str = "RefuseAllSamplingMediator"
    reason: str = "operator policy disables sampling"

    async def mediate(
        self,
        request: SamplingRequest,
    ) -> SamplingRefused:
        return SamplingRefused(reason=self.reason, rule="sampling-disabled-by-operator")


@dataclass(frozen=True)
class AllowlistSamplingMediator:
    """Mediator that delegates to an inner mediator only for an
    operator-declared allowlist of upstream MCP servers.

    Other servers' sampling requests are refused with a clear
    explanation referencing the allowlist policy.
    """

    name: str = "AllowlistSamplingMediator"
    allowed_servers: frozenset[str] = field(default_factory=frozenset)
    inner: Any = None

    async def mediate(
        self,
        request: SamplingRequest,
    ) -> SamplingResponse | SamplingRefused:
        if request.requesting_server not in self.allowed_servers:
            return SamplingRefused(
                reason=(f"server {request.requesting_server!r} is not on the sampling allowlist"),
                rule="sampling-server-not-allowed",
            )
        if self.inner is None:
            return SamplingRefused(
                reason="allowlist passed but no inner mediator configured",
                rule="sampling-no-inner",
            )
        return await self.inner.mediate(request)
