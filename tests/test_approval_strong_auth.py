from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction, ApprovalRequest, ApprovalStatus
from capabledeputy.approval.strong_auth import (
    approval_requires_strong_auth,
    approval_to_client_dict,
)
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.policy.labels import LabelState, tags_for_labels_strings


@pytest.fixture
def app(tmp_path: Path) -> App:
    return App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")


def _request(action: ApprovalAction, labels: tuple[str, ...] = ()) -> ApprovalRequest:
    labels_in = tags_for_labels_strings(frozenset(labels)) if labels else LabelState()
    return ApprovalRequest(
        id=1,
        audit_id=uuid4(),
        from_session=uuid4(),
        action=action,
        payload="payload",
        target="target",
        labels_in=labels_in,
        labels_out=LabelState(),
        capability_requested=None,
        justification="because",
        status=ApprovalStatus.PENDING,
    )


def test_approval_requires_strong_auth_for_purchase_and_destructive() -> None:
    assert approval_requires_strong_auth(_request(ApprovalAction.QUEUE_PURCHASE))
    assert approval_requires_strong_auth(_request(ApprovalAction.EXECUTE_DESTRUCTIVE))


def test_approval_requires_strong_auth_for_sensitive_labels() -> None:
    labels_in = tags_for_labels_strings(frozenset({"confidential.financial"}))
    request = _request(ApprovalAction.SEND_EMAIL)
    request = ApprovalRequest(
        id=request.id,
        audit_id=request.audit_id,
        from_session=request.from_session,
        action=request.action,
        payload=request.payload,
        target=request.target,
        labels_in=labels_in,
        labels_out=request.labels_out,
        capability_requested=request.capability_requested,
        justification=request.justification,
        status=request.status,
    )
    assert approval_requires_strong_auth(request)


def test_approval_to_client_dict_includes_policy_flags() -> None:
    payload = approval_to_client_dict(
        _request(ApprovalAction.QUEUE_PURCHASE),
        touch_id_policy_enabled=True,
    )
    assert payload["requires_strong_auth"] is True
    assert payload["touch_id_policy_enabled"] is True


async def test_approval_list_includes_strong_auth_fields(app: App) -> None:
    await app.approval_queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.QUEUE_PURCHASE,
        payload="buy",
        target="vendor",
        labels_in=LabelState(),
        labels_out=LabelState(),
        capability_requested=None,
        justification="test",
    )
    handlers = make_approval_handlers(app)
    result = await handlers["approval.list"]({"status": "pending"})
    approval = result["approvals"][0]
    assert approval["requires_strong_auth"] is True
    assert "touch_id_policy_enabled" in approval
