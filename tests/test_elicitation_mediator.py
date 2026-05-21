"""Tests for the ElicitationMediator port + builtins."""

from __future__ import annotations

import pytest

from capabledeputy.substrate.elicitation_mediators_builtin import (
    AllowlistElicitationMediator,
    ApprovalQueueElicitationMediator,
    RefuseAllElicitationMediator,
)
from capabledeputy.substrate.elicitation_port import (
    ElicitationRefused,
    ElicitationRequest,
)


@pytest.mark.asyncio
async def test_refuse_all_refuses() -> None:
    mediator = RefuseAllElicitationMediator(reason="testing")
    request = ElicitationRequest(prompt="What is your favorite color?")
    response = await mediator.mediate(request)
    assert isinstance(response, ElicitationRefused)
    assert "testing" in response.reason
    assert response.rule == "elicitation-disabled-by-operator"


@pytest.mark.asyncio
async def test_allowlist_refuses_unlisted() -> None:
    inner = RefuseAllElicitationMediator()  # any inner; won't be called
    mediator = AllowlistElicitationMediator(
        allowed_servers=frozenset({"trusted"}),
        inner=inner,
    )
    request = ElicitationRequest(
        prompt="hello",
        requesting_server="evil",
    )
    response = await mediator.mediate(request)
    assert isinstance(response, ElicitationRefused)
    assert response.rule == "elicitation-server-not-allowed"
    assert "evil" in response.reason


@pytest.mark.asyncio
async def test_allowlist_delegates_listed() -> None:
    inner = RefuseAllElicitationMediator(reason="inner-refused")
    mediator = AllowlistElicitationMediator(
        allowed_servers=frozenset({"trusted"}),
        inner=inner,
    )
    request = ElicitationRequest(
        prompt="hello",
        requesting_server="trusted",
    )
    response = await mediator.mediate(request)
    # Inner refused; we get its specific refusal not the allowlist's
    assert isinstance(response, ElicitationRefused)
    assert response.rule == "elicitation-disabled-by-operator"
    assert "inner-refused" in response.reason


@pytest.mark.asyncio
async def test_allowlist_no_inner_refuses() -> None:
    mediator = AllowlistElicitationMediator(
        allowed_servers=frozenset({"x"}),
        inner=None,
    )
    request = ElicitationRequest(prompt="?", requesting_server="x")
    response = await mediator.mediate(request)
    assert isinstance(response, ElicitationRefused)
    assert response.rule == "elicitation-no-inner"


@pytest.mark.asyncio
async def test_approval_queue_mediator_no_queue_refuses() -> None:
    mediator = ApprovalQueueElicitationMediator(approval_queue=None)
    request = ElicitationRequest(prompt="?")
    response = await mediator.mediate(request)
    assert isinstance(response, ElicitationRefused)
    assert response.rule == "elicitation-no-queue"


@pytest.mark.asyncio
async def test_approval_queue_mediator_routing_not_yet_implemented() -> None:
    """The port is in place; the actual queue submission is a follow-up."""

    class _FakeQueue:
        pass

    mediator = ApprovalQueueElicitationMediator(approval_queue=_FakeQueue())
    request = ElicitationRequest(prompt="?", requesting_server="x")
    response = await mediator.mediate(request)
    # Documented limitation surfaced as a clear refusal until wired
    assert isinstance(response, ElicitationRefused)
    assert response.rule == "elicitation-routing-not-implemented"
