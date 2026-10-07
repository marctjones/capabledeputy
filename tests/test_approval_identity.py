"""Durable review identities cannot alias another action after daemon restart."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.queue import ApprovalNotFoundError, ApprovalQueue, ApprovalStateError
from capabledeputy.policy.labels import LabelState


async def _submit(queue: ApprovalQueue):
    return await queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        target="recipient@example.test",
        payload="reviewed content",
        labels_in=LabelState(),
        justification="test exact review",
    )


async def test_restart_does_not_reuse_review_id_even_when_clock_moves_back(tmp_path, monkeypatch):
    path = tmp_path / "approval-ids.sqlite3"
    monkeypatch.setattr(
        "capabledeputy.approval.queue.time.time_ns", lambda: 1_700_000_000_000_000_000
    )
    first = await _submit(ApprovalQueue(id_store_path=path))
    assert 1_000_000_000_000 < first.id < (1 << 53)
    monkeypatch.setattr("capabledeputy.approval.queue.time.time_ns", lambda: 0)
    restarted = ApprovalQueue(id_store_path=path)
    second = await _submit(restarted)
    assert second.id == first.id + 1
    with pytest.raises(ApprovalNotFoundError):
        restarted.get(first.id)
    assert restarted.get(second.id) == second


def test_independent_allocators_share_one_atomic_counter(tmp_path):
    path = tmp_path / "approval-ids.sqlite3"
    first = ApprovalQueue(id_store_path=path)._allocate_id()
    queues = [ApprovalQueue(id_store_path=path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(queue._allocate_id) for queue in queues for _ in range(10)]
        identifiers = [future.result() for future in futures]
    assert sorted(identifiers) == list(range(first + 1, first + 41))


@pytest.mark.parametrize("missing", ["database", "marker"])
def test_lost_allocator_or_identity_fails_closed(tmp_path, missing):
    path = tmp_path / "approval-ids.sqlite3"
    ApprovalQueue(id_store_path=path)._allocate_id()
    target = path if missing == "database" else path.with_name(path.name + ".identity")
    target.rename(target.with_suffix(".quarantined"))
    with pytest.raises(ApprovalStateError, match="incomplete"):
        ApprovalQueue(id_store_path=path)._allocate_id()


def test_replaced_allocator_namespace_fails_closed(tmp_path):
    path = tmp_path / "approval-ids.sqlite3"
    ApprovalQueue(id_store_path=path)._allocate_id()
    with sqlite3.connect(path) as con:
        con.execute("UPDATE approval_ids SET namespace = ?", (str(uuid4()),))
    with pytest.raises(ApprovalStateError, match="identity changed"):
        ApprovalQueue(id_store_path=path)._allocate_id()


@pytest.mark.parametrize("value", [0, (1 << 53) - 1])
def test_invalid_or_exhausted_counter_fails_closed(tmp_path, value):
    path = tmp_path / "approval-ids.sqlite3"
    ApprovalQueue(id_store_path=path)._allocate_id()
    with sqlite3.connect(path) as con:
        con.execute("UPDATE approval_ids SET next_id = ?", (value,))
    with pytest.raises(ApprovalStateError, match="invalid or exhausted"):
        ApprovalQueue(id_store_path=path)._allocate_id()


def test_missing_counter_and_corrupt_database_fail_closed(tmp_path):
    path = tmp_path / "approval-ids.sqlite3"
    ApprovalQueue(id_store_path=path)._allocate_id()
    with sqlite3.connect(path) as con:
        con.execute("DELETE FROM approval_ids")
    with pytest.raises(ApprovalStateError, match="identity changed"):
        ApprovalQueue(id_store_path=path)._allocate_id()
    path.write_bytes(b"corrupt database")
    with pytest.raises(sqlite3.DatabaseError):
        ApprovalQueue(id_store_path=path)._allocate_id()
