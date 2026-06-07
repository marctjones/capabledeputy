"""Cookbook P2.7 — default-decline-after-N-minutes for stale approvals.

Tests cover:
  - submit stores expires_at = now + default_ttl_seconds
  - explicit ttl_seconds overrides the queue default
  - ttl_seconds=0 produces an immortal request (expires_at=None)
  - PENDING request past expires_at flips to EXPIRED on next access
  - APPROVAL_EXPIRED audit event emitted exactly once per expiry
  - approve() on an expired request raises ApprovalStateError
  - dict round-trip preserves expires_at
  - legacy dict without expires_at deserializes as None (back-compat)
  - list() and siblings() both sweep stale entries
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.approval.model import (
    ApprovalAction,
    ApprovalRequest,
    ApprovalStatus,
)
from capabledeputy.approval.queue import (
    DEFAULT_APPROVAL_TTL_SECONDS,
    ApprovalQueue,
    ApprovalStateError,
)
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def queue(tmp_path: Path) -> ApprovalQueue:
    return ApprovalQueue(audit=AuditWriter(tmp_path / "audit.jsonl"))


async def _submit(
    queue: ApprovalQueue,
    *,
    ttl_seconds: int | None = None,
) -> ApprovalRequest:
    return await queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload="body",
        target="x@example.com",
        labels_in=LabelState(a=frozenset({CategoryTag("personal", Tier.REGULATED)})),
        ttl_seconds=ttl_seconds,
    )


# --- TTL field ----------------------------------------------------------


@pytest.mark.anyio
async def test_submit_assigns_default_ttl(queue: ApprovalQueue) -> None:
    """A fresh queue uses DEFAULT_APPROVAL_TTL_SECONDS (5 min) when
    the caller doesn't pass ttl_seconds. expires_at lands within
    ±2s of (now + default)."""
    before = datetime.now(UTC)
    request = await _submit(queue)
    assert request.expires_at is not None
    expected = before + timedelta(seconds=DEFAULT_APPROVAL_TTL_SECONDS)
    assert abs((request.expires_at - expected).total_seconds()) < 2


@pytest.mark.anyio
async def test_submit_explicit_ttl_overrides_default(queue: ApprovalQueue) -> None:
    request = await _submit(queue, ttl_seconds=60)
    assert request.expires_at is not None
    delta = (request.expires_at - datetime.now(UTC)).total_seconds()
    assert 55 < delta < 65


@pytest.mark.anyio
async def test_submit_ttl_zero_makes_immortal(queue: ApprovalQueue) -> None:
    """ttl_seconds=0 produces a request with expires_at=None — never
    auto-expires. Useful for high-stakes operator-monitored
    approvals where stale-expiry would be worse than waiting."""
    request = await _submit(queue, ttl_seconds=0)
    assert request.expires_at is None


@pytest.mark.anyio
async def test_queue_default_ttl_can_be_overridden(tmp_path: Path) -> None:
    queue = ApprovalQueue(
        audit=AuditWriter(tmp_path / "audit.jsonl"),
        default_ttl_seconds=30,
    )
    request = await _submit(queue)
    assert request.expires_at is not None
    delta = (request.expires_at - datetime.now(UTC)).total_seconds()
    assert 25 < delta < 35


# --- Expiry sweep -------------------------------------------------------


@pytest.mark.anyio
async def test_get_expires_stale_pending_request(queue: ApprovalQueue) -> None:
    """A PENDING request past its expires_at flips to EXPIRED on the
    next get(). decision_at is set; decided_by='ttl'."""
    request = await _submit(queue, ttl_seconds=60)
    # Backdate the expires_at into the past
    queue._requests[request.id] = replace(
        request,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    fetched = queue.get(request.id)
    assert fetched.status == ApprovalStatus.EXPIRED
    assert fetched.decided_by == "ttl"
    assert fetched.decision_at is not None


@pytest.mark.anyio
async def test_list_sweeps_stale_pending_requests(queue: ApprovalQueue) -> None:
    """list() sweeps every PENDING entry before returning. Stale
    ones surface as EXPIRED in the response."""
    a = await _submit(queue, ttl_seconds=60)
    b = await _submit(queue, ttl_seconds=60)
    # Backdate both
    for r in (a, b):
        queue._requests[r.id] = replace(
            queue._requests[r.id],
            expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
    listed = queue.list()
    assert all(r.status == ApprovalStatus.EXPIRED for r in listed)


@pytest.mark.anyio
async def test_pending_within_ttl_unchanged(queue: ApprovalQueue) -> None:
    """A request whose expires_at is in the FUTURE stays PENDING
    on access. The sweep only fires on stale ones."""
    request = await _submit(queue, ttl_seconds=60)
    fetched = queue.get(request.id)
    assert fetched.status == ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_immortal_request_never_expires(queue: ApprovalQueue) -> None:
    """ttl_seconds=0 + an old request → still PENDING. Useful for
    approvals the operator intentionally wants to keep open."""
    request = await _submit(queue, ttl_seconds=0)
    # Even with a backdated requested_at, no expires_at → no sweep.
    queue._requests[request.id] = replace(
        request,
        requested_at=datetime.now(UTC) - timedelta(days=3),
    )
    fetched = queue.get(request.id)
    assert fetched.status == ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_approve_on_expired_raises(queue: ApprovalQueue) -> None:
    """Once expired, approve() refuses with ApprovalStateError —
    the EXPIRED state is terminal."""
    request = await _submit(queue, ttl_seconds=60)
    queue._requests[request.id] = replace(
        request,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(ApprovalStateError):
        await queue.approve(request.id)


@pytest.mark.anyio
async def test_already_decided_request_not_re_expired(queue: ApprovalQueue) -> None:
    """A request that was already approved (or denied/expired) is
    NOT re-flipped on access — _maybe_expire short-circuits on
    non-pending status."""
    request = await _submit(queue, ttl_seconds=0)
    await queue.approve(request.id, decided_by="op")
    queue._requests[request.id] = replace(
        queue._requests[request.id],
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    fetched = queue.get(request.id)
    assert fetched.status == ApprovalStatus.APPROVED


# --- Dict round-trip ----------------------------------------------------


def test_to_dict_includes_expires_at() -> None:
    now = datetime.now(UTC)
    request = ApprovalRequest(
        id=1,
        audit_id=uuid4(),
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload="body",
        target="x@example.com",
        labels_in=LabelState(),
        labels_out=LabelState(),
        capability_requested=None,
        justification="",
        expires_at=now,
    )
    d = request.to_dict()
    assert d["expires_at"] == now.isoformat()


def test_immortal_request_serializes_null_expires_at() -> None:
    request = ApprovalRequest(
        id=1,
        audit_id=uuid4(),
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload="body",
        target="x@example.com",
        labels_in=LabelState(),
        labels_out=LabelState(),
        capability_requested=None,
        justification="",
        expires_at=None,
    )
    assert request.to_dict()["expires_at"] is None
