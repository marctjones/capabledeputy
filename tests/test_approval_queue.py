from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.approval.model import ApprovalAction, ApprovalStatus
from capabledeputy.approval.queue import (
    ApprovalNotFoundError,
    ApprovalQueue,
    ApprovalStateError,
)
from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.labels import CategoryTag, LabelState, Tier


@pytest.fixture
def writer(tmp_path: Path) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


@pytest.fixture
def queue(writer: AuditWriter) -> ApprovalQueue:
    return ApprovalQueue(audit=writer)


async def test_submit_assigns_monotonic_ids(queue: ApprovalQueue) -> None:
    sid = uuid4()
    a = await queue.submit(
        from_session=sid,
        action=ApprovalAction.DECLASSIFY,
        payload="x",
        target="y",
        labels_in=LabelState(),
    )
    b = await queue.submit(
        from_session=sid,
        action=ApprovalAction.DECLASSIFY,
        payload="x",
        target="y",
        labels_in=LabelState(),
    )
    assert b.id == a.id + 1


async def test_get_unknown_raises(queue: ApprovalQueue) -> None:
    with pytest.raises(ApprovalNotFoundError):
        queue.get(999)  # type: ignore


async def test_approve_marks_status_and_logs(
    queue: ApprovalQueue,
    writer: AuditWriter,
) -> None:
    sid = uuid4()
    req = await queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="hi",
        target="alice@example.com",
        labels_in=LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )
    decided = await queue.approve(req.id, decided_by="marc")
    assert decided.status == ApprovalStatus.APPROVED
    assert decided.decided_by == "marc"
    events = [e for e in await writer.read_all() if e.event_type == EventType.APPROVAL_APPROVED]
    assert len(events) == 1


async def test_deny_marks_status(queue: ApprovalQueue) -> None:
    sid = uuid4()
    req = await queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="x",
        target="y",
        labels_in=LabelState(),
    )
    decided = await queue.deny(req.id, reason="not now")
    assert decided.status == ApprovalStatus.DENIED


async def test_defer_marks_status(queue: ApprovalQueue) -> None:
    sid = uuid4()
    req = await queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="x",
        target="y",
        labels_in=LabelState(),
    )
    decided = await queue.defer(req.id)
    assert decided.status == ApprovalStatus.DEFERRED


async def test_approve_already_decided_raises(queue: ApprovalQueue) -> None:
    sid = uuid4()
    req = await queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="x",
        target="y",
        labels_in=LabelState(),
    )
    await queue.approve(req.id)  # type: ignore
    with pytest.raises(ApprovalStateError):
        await queue.approve(req.id)  # type: ignore


async def test_list_filters_by_status(queue: ApprovalQueue) -> None:
    sid = uuid4()
    a = await queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="x",
        target="y",
        labels_in=LabelState(),
    )
    b = await queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="x",
        target="y",
        labels_in=LabelState(),
    )
    await queue.approve(a.id)  # type: ignore

    pending = queue.list(status=ApprovalStatus.PENDING)
    approved = queue.list(status=ApprovalStatus.APPROVED)
    assert {p.id for p in pending} == {b.id}
    assert {p.id for p in approved} == {a.id}


async def test_round_trip_to_dict() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4 as new_uuid

    from capabledeputy.approval.model import ApprovalRequest

    req = ApprovalRequest(
        id=1,
        audit_id=new_uuid(),
        from_session=new_uuid(),
        action=ApprovalAction.SEND_EMAIL,
        payload="hello",
        target="alice@x.com",
        labels_in=LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
        labels_out=LabelState(),
        capability_requested=None,
        justification="user asked to share",
        requested_at=datetime.now(UTC),
    )
    d = req.to_dict()
    assert d["payload"] == "hello"
    # Verify it serializes correctly
    assert d["labels_in"] is not None
    assert d["status"] == "pending"
