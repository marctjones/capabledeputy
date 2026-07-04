"""Tests for the ElicitationMediator port + builtins."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.queue import ApprovalQueue
from capabledeputy.substrate.elicitation_mediators_builtin import (
    AllowlistElicitationMediator,
    ApprovalQueueElicitationMediator,
    RefuseAllElicitationMediator,
)
from capabledeputy.substrate.elicitation_port import (
    ElicitationRefused,
    ElicitationRequest,
    ElicitationResponse,
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
async def test_approval_queue_mediator_requires_session() -> None:
    mediator = ApprovalQueueElicitationMediator(approval_queue=ApprovalQueue())
    request = ElicitationRequest(prompt="?", requesting_server="x")
    response = await mediator.mediate(request)
    assert isinstance(response, ElicitationRefused)
    assert response.rule == "elicitation-no-session"


@pytest.mark.asyncio
async def test_approval_queue_mediator_routes_and_waits_for_response() -> None:
    queue = ApprovalQueue()
    session_id = uuid4()
    mediator = ApprovalQueueElicitationMediator(
        approval_queue=queue,
        timeout_seconds=2.0,
    )

    async def decide() -> None:
        while not queue.list():
            await asyncio.sleep(0)
        queued = queue.list()[0]
        assert queued.action == ApprovalAction.ELICITATION
        assert queued.target == "calendar"
        await queue.complete_elicitation(
            queued.id,
            response_value={"date": "2026-07-04"},
            decided_by="test",
        )

    task = asyncio.create_task(decide())
    response = await mediator.mediate(
        ElicitationRequest(
            prompt="Pick a date",
            schema={"type": "object"},
            requesting_server="calendar",
            session_id=session_id,
            response_inherent_labels=frozenset({"trusted.user_direct"}),
        ),
    )
    await task

    assert isinstance(response, ElicitationResponse)
    assert response.response_value == {"date": "2026-07-04"}
    assert response.applied_labels == frozenset({"trusted.user_direct"})


@pytest.mark.asyncio
async def test_approval_queue_mediator_fails_closed_when_approved_without_response() -> None:
    queue = ApprovalQueue()
    session_id = uuid4()
    mediator = ApprovalQueueElicitationMediator(
        approval_queue=queue,
        timeout_seconds=2.0,
    )

    async def approve_without_response() -> None:
        while not queue.list():
            await asyncio.sleep(0)
        await queue.approve(queue.list()[0].id, decided_by="test")

    task = asyncio.create_task(approve_without_response())
    response = await mediator.mediate(
        ElicitationRequest(
            prompt="Pick a date",
            requesting_server="calendar",
            session_id=session_id,
        ),
    )
    await task

    assert isinstance(response, ElicitationRefused)
    assert response.rule == "elicitation-approval-queue-failed"
    assert "without elicitation_response" in response.reason
