"""Tests for the SamplingMediator port + builtins.

The mediator is the chokepoint for MCP sampling requests. Operators
register a mediator that either routes to the LLM, refuses outright,
or applies allowlist policy.
"""

from __future__ import annotations

import pytest

from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from capabledeputy.substrate.sampling_mediators_builtin import (
    AllowlistSamplingMediator,
    LiteLLMSamplingMediator,
    RefuseAllSamplingMediator,
)
from capabledeputy.substrate.sampling_port import (
    SamplingRefused,
    SamplingRequest,
    SamplingResponse,
)


@pytest.mark.asyncio
async def test_litellm_mediator_routes_to_client() -> None:
    fake = FakeLLMClient(
        [LLMResponse(content="mediated reply", finish_reason=FinishReason.STOP)],
    )
    mediator = LiteLLMSamplingMediator(llm_client=fake)
    request = SamplingRequest(
        messages=({"role": "user", "content": "hello"},),
        requesting_server="upstream-x",
    )
    response = await mediator.mediate(request)
    assert isinstance(response, SamplingResponse)
    assert response.content == "mediated reply"
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_litellm_mediator_with_no_client_refuses() -> None:
    mediator = LiteLLMSamplingMediator(llm_client=None)
    request = SamplingRequest(
        messages=({"role": "user", "content": "hello"},),
    )
    response = await mediator.mediate(request)
    assert isinstance(response, SamplingRefused)
    assert response.rule == "sampling-no-llm"


@pytest.mark.asyncio
async def test_refuse_all_mediator() -> None:
    mediator = RefuseAllSamplingMediator(reason="testing")
    request = SamplingRequest(
        messages=({"role": "user", "content": "hi"},),
    )
    response = await mediator.mediate(request)
    assert isinstance(response, SamplingRefused)
    assert "testing" in response.reason
    assert response.rule == "sampling-disabled-by-operator"


@pytest.mark.asyncio
async def test_allowlist_mediator_allows_listed() -> None:
    inner = LiteLLMSamplingMediator(
        llm_client=FakeLLMClient([LLMResponse(content="ok", finish_reason=FinishReason.STOP)]),
    )
    mediator = AllowlistSamplingMediator(
        allowed_servers=frozenset({"trusted-server"}),
        inner=inner,
    )
    request = SamplingRequest(
        messages=({"role": "user", "content": "hi"},),
        requesting_server="trusted-server",
    )
    response = await mediator.mediate(request)
    assert isinstance(response, SamplingResponse)
    assert response.content == "ok"


@pytest.mark.asyncio
async def test_allowlist_mediator_refuses_unlisted() -> None:
    inner = LiteLLMSamplingMediator(
        llm_client=FakeLLMClient([LLMResponse(content="x", finish_reason=FinishReason.STOP)]),
    )
    mediator = AllowlistSamplingMediator(
        allowed_servers=frozenset({"trusted-server"}),
        inner=inner,
    )
    request = SamplingRequest(
        messages=({"role": "user", "content": "hi"},),
        requesting_server="evil-server",
    )
    response = await mediator.mediate(request)
    assert isinstance(response, SamplingRefused)
    assert response.rule == "sampling-server-not-allowed"
    assert "evil-server" in response.reason


@pytest.mark.asyncio
async def test_allowlist_mediator_with_no_inner_refuses() -> None:
    mediator = AllowlistSamplingMediator(
        allowed_servers=frozenset({"x"}),
        inner=None,
    )
    request = SamplingRequest(
        messages=({"role": "user", "content": "hi"},),
        requesting_server="x",
    )
    response = await mediator.mediate(request)
    assert isinstance(response, SamplingRefused)
    assert response.rule == "sampling-no-inner"


@pytest.mark.asyncio
async def test_sampling_response_carries_inherent_labels() -> None:
    fake = FakeLLMClient([LLMResponse(content="r", finish_reason=FinishReason.STOP)])
    mediator = LiteLLMSamplingMediator(llm_client=fake)
    request = SamplingRequest(
        messages=({"role": "user", "content": "hi"},),
        response_inherent_labels=frozenset({"untrusted.external"}),
    )
    response = await mediator.mediate(request)
    assert isinstance(response, SamplingResponse)
    # Operator-curated inherent labels flow through to the response
    assert "untrusted.external" in response.applied_labels


@pytest.mark.asyncio
async def test_sampling_request_with_system_prompt() -> None:
    """The mediator prepends the system_prompt to the messages."""
    captured_messages = []

    class _CapturingLLM:
        async def respond(self, messages, tools):
            captured_messages.extend(messages)
            return LLMResponse(content="ok", finish_reason=FinishReason.STOP)

    mediator = LiteLLMSamplingMediator(llm_client=_CapturingLLM())
    request = SamplingRequest(
        messages=({"role": "user", "content": "ask"},),
        system_prompt="You are a helpful assistant.",
    )
    await mediator.mediate(request)
    # First message should be the system prompt
    assert captured_messages[0].role.value == "system"
    assert "helpful" in captured_messages[0].content
