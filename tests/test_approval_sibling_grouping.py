"""Cookbook P2.1 — sibling-grouping in the approval queue.

Tests cover:
  - two submits in quick succession with the same (session, action,
    target) get the same sibling_group_id
  - the prior request is back-stamped with the new group id
  - approve_group resolves every PENDING sibling in one call
  - a submit OUTSIDE the grouping window does NOT merge
  - different target / different action → independent
  - already-decided siblings are skipped by approve_group
"""

from __future__ import annotations

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
    SIBLING_GROUPING_WINDOW,
    ApprovalQueue,
)
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.labels import CategoryTag, LabelState, Tier


@pytest.fixture
async def queue(tmp_path: Path) -> ApprovalQueue:
    return ApprovalQueue(audit=AuditWriter(tmp_path / "audit.jsonl"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _submit(
    queue: ApprovalQueue,
    session_id,
    action: ApprovalAction,
    target: str,
    payload: str = "body",
) -> ApprovalRequest:
    return await queue.submit(
        from_session=session_id,
        action=action,
        payload=payload,
        target=target,
        labels_in=LabelState(
            a=frozenset(
                {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )


# --- Sibling detection ---------------------------------------------------


@pytest.mark.anyio
async def test_two_quick_submits_share_sibling_group(queue: ApprovalQueue) -> None:
    """A second submit within the grouping window for the same
    (session, action, target) carries the same sibling_group_id.
    The prior request is back-stamped — both reference the new id."""
    sid = uuid4()
    a = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "hi 1")
    b = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "hi 2")
    # Both must end up with the same group id
    a_after = queue.get(a.id)
    b_after = queue.get(b.id)
    assert a_after.sibling_group_id is not None
    assert a_after.sibling_group_id == b_after.sibling_group_id


@pytest.mark.anyio
async def test_third_sibling_joins_existing_group(queue: ApprovalQueue) -> None:
    """Three quick submits → one group of three. The third sibling
    joins the existing group rather than minting a new one."""
    sid = uuid4()
    a = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "1")
    b = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "2")
    c = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "3")
    group = queue.get(a.id).sibling_group_id
    assert group is not None
    assert queue.get(b.id).sibling_group_id == group
    assert queue.get(c.id).sibling_group_id == group
    assert len(queue.siblings(group)) == 3


@pytest.mark.anyio
async def test_different_target_no_grouping(queue: ApprovalQueue) -> None:
    """Two sends to DIFFERENT recipients are NOT siblings even when
    submitted in the same instant — the cookbook's grouping is
    intentionally narrow to keep the approve-all gesture safe."""
    sid = uuid4()
    a = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "x")
    b = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "stranger@x.com", "y")
    assert queue.get(a.id).sibling_group_id is None
    assert queue.get(b.id).sibling_group_id is None


@pytest.mark.anyio
async def test_different_action_no_grouping(queue: ApprovalQueue) -> None:
    """A send to spouse and a purchase to amazon — different action
    classes — never group."""
    sid = uuid4()
    a = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "x")
    b = await _submit(queue, sid, ApprovalAction.QUEUE_PURCHASE, "spouse@x.com", "y")
    assert queue.get(a.id).sibling_group_id is None
    assert queue.get(b.id).sibling_group_id is None


@pytest.mark.anyio
async def test_different_session_no_grouping(queue: ApprovalQueue) -> None:
    """Two distinct sessions never share a sibling group — even if
    the same operator is behind both. Grouping is a per-session
    approval-economy mechanism, not a cross-session merge."""
    a = await _submit(queue, uuid4(), ApprovalAction.SEND_EMAIL, "spouse@x.com", "x")
    b = await _submit(queue, uuid4(), ApprovalAction.SEND_EMAIL, "spouse@x.com", "y")
    assert queue.get(a.id).sibling_group_id is None
    assert queue.get(b.id).sibling_group_id is None


@pytest.mark.anyio
async def test_outside_window_no_grouping(queue: ApprovalQueue) -> None:
    """A second submit AFTER the grouping window elapsed does NOT
    merge — the operator's attention has reset; a fresh card is
    appropriate."""
    from dataclasses import replace

    sid = uuid4()
    a = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "x")
    # Backdate the first request to be outside the window.
    queue._requests[a.id] = replace(
        a,
        requested_at=datetime.now(UTC) - SIBLING_GROUPING_WINDOW - timedelta(seconds=1),
    )
    b = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "y")
    assert queue.get(b.id).sibling_group_id is None


# --- approve_group -------------------------------------------------------


@pytest.mark.anyio
async def test_approve_group_resolves_every_pending_sibling(
    queue: ApprovalQueue,
) -> None:
    sid = uuid4()
    a = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "1")
    b = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "2")
    group = queue.get(a.id).sibling_group_id
    assert group is not None
    approved = await queue.approve_group(group, decided_by="op")
    assert len(approved) == 2
    assert queue.get(a.id).status == ApprovalStatus.APPROVED
    assert queue.get(b.id).status == ApprovalStatus.APPROVED


@pytest.mark.anyio
async def test_approve_group_skips_already_decided(queue: ApprovalQueue) -> None:
    """If the operator denied one sibling individually before clicking
    approve-all, approve_group MUST NOT re-approve the denied one."""
    sid = uuid4()
    a = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "1")
    b = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "2")
    group = queue.get(a.id).sibling_group_id
    assert group is not None
    await queue.deny(a.id, decided_by="op", reason="wrong content")
    approved = await queue.approve_group(group, decided_by="op")
    # Only b was pending; a was already decided.
    assert [r.id for r in approved] == [b.id]
    assert queue.get(a.id).status == ApprovalStatus.DENIED
    assert queue.get(b.id).status == ApprovalStatus.APPROVED


@pytest.mark.anyio
async def test_to_dict_surfaces_sibling_group_id(queue: ApprovalQueue) -> None:
    """Sibling group id is exposed in the dict serialization so the
    approval-list RPC and the chat REPL can render the grouping."""
    sid = uuid4()
    a = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "1")
    b = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "2")
    a_dict = queue.get(a.id).to_dict()
    b_dict = queue.get(b.id).to_dict()
    assert a_dict["sibling_group_id"] is not None
    assert a_dict["sibling_group_id"] == b_dict["sibling_group_id"]


@pytest.mark.anyio
async def test_solo_request_has_no_sibling_group(queue: ApprovalQueue) -> None:
    """The common case: one approval, no grouping. sibling_group_id
    is None and to_dict serializes it as null."""
    sid = uuid4()
    a = await _submit(queue, sid, ApprovalAction.SEND_EMAIL, "spouse@x.com", "1")
    assert queue.get(a.id).sibling_group_id is None
    assert queue.get(a.id).to_dict()["sibling_group_id"] is None
