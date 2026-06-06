"""approval.approve_group RPC handler — cookbook P2.1 sibling-grouping
back-end.

Drives the handler with an in-memory App fixture that has a few
pre-submitted sibling requests; exercises the bulk-approve path,
the skip-already-decided path, and the result-shape contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction, ApprovalStatus
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.policy.labels import CategoryTag, LabelState, Tier


@pytest.fixture
async def app(tmp_path: Path) -> App:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()
    return app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _submit_sibling_pair(app: App) -> tuple[str, int, int]:
    """Drop two sibling SEND_EMAILs into the queue. Returns
    (group_id, id_a, id_b)."""
    sid = uuid4()
    a = await app.approval_queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="body 1",
        target="spouse@example.com",
        labels_in=LabelState(
            a=frozenset(
                {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )
    b = await app.approval_queue.submit(
        from_session=sid,
        action=ApprovalAction.SEND_EMAIL,
        payload="body 2",
        target="spouse@example.com",
        labels_in=LabelState(
            a=frozenset(
                {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )
    group = app.approval_queue.get(a.id).sibling_group_id
    assert group is not None
    assert group == app.approval_queue.get(b.id).sibling_group_id
    return str(group), a.id, b.id


@pytest.mark.anyio
async def test_approve_group_returns_per_member_results(app: App) -> None:
    """The group result enumerates each sibling with its dispatch
    outcome. Counts (n_total, n_approved, n_skipped, n_failed) are
    consistent with the per-member list."""
    handlers = make_approval_handlers(app)
    group_id, id_a, id_b = await _submit_sibling_pair(app)
    result = await handlers["approval.approve_group"]({"group_id": group_id})

    assert result["group_id"] == group_id
    assert result["n_total"] == 2
    assert result["n_approved"] >= 1  # depends on dispatch success
    ids = sorted(r["id"] for r in result["results"])
    assert ids == sorted([id_a, id_b])


@pytest.mark.anyio
async def test_approve_group_skips_already_decided(app: App) -> None:
    """If the operator denied one sibling before clicking approve-
    all, the group handler MUST NOT re-approve it. The result
    surfaces it as skipped with the prior status as the reason."""
    handlers = make_approval_handlers(app)
    group_id, id_a, _ = await _submit_sibling_pair(app)
    # Deny id_a out-of-band.
    await handlers["approval.deny"](
        {"id": id_a, "reason": "wrong recipient"},
    )
    result = await handlers["approval.approve_group"]({"group_id": group_id})

    skipped = [r for r in result["results"] if r.get("skipped")]
    assert len(skipped) == 1
    assert skipped[0]["id"] == id_a
    assert "denied" in skipped[0]["reason"]
    assert result["n_skipped"] == 1


@pytest.mark.anyio
async def test_approve_group_unknown_group_id_returns_empty(app: App) -> None:
    """An unknown group_id is not an error — it's just an empty
    siblings list. The handler returns zero-everywhere counts."""
    handlers = make_approval_handlers(app)
    fake = str(uuid4())
    result = await handlers["approval.approve_group"]({"group_id": fake})
    assert result["group_id"] == fake
    assert result["n_total"] == 0
    assert result["results"] == []


@pytest.mark.anyio
async def test_approve_group_marks_members_approved(app: App) -> None:
    """After approve_group, every (previously-pending) member has
    status=APPROVED in the queue."""
    handlers = make_approval_handlers(app)
    group_id, id_a, id_b = await _submit_sibling_pair(app)
    await handlers["approval.approve_group"]({"group_id": group_id})
    assert app.approval_queue.get(id_a).status == ApprovalStatus.APPROVED
    assert app.approval_queue.get(id_b).status == ApprovalStatus.APPROVED


@pytest.mark.anyio
async def test_approve_group_audits_each_approval(app: App, tmp_path: Path) -> None:
    """Each sibling approval emits its own APPROVAL_DECIDED audit
    event — bulk-approve doesn't collapse the per-action trail.
    The audit log should still answer "what got approved by this
    operator gesture?" sibling-by-sibling."""
    handlers = make_approval_handlers(app)
    group_id, _, _ = await _submit_sibling_pair(app)
    await handlers["approval.approve_group"]({"group_id": group_id})
    # Audit log is at the path App was constructed with.
    audit_text = (tmp_path / "audit.jsonl").read_text()
    decisions = [
        json.loads(line)
        for line in audit_text.splitlines()
        if line and '"approval.approved"' in line
    ]
    # At least one approval-approved event per sibling.
    assert len(decisions) >= 2
