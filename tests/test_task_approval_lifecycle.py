"""Task reviews retain evidence without restoring or widening authority."""

from uuid import UUID, uuid4

import pytest

from capabledeputy.approval.model import ApprovalAction, ApprovalStatus
from capabledeputy.approval.queue import ApprovalQueue
from capabledeputy.daemon.authz import requires_operator
from capabledeputy.ipc.client import DaemonError
from capabledeputy.policy.labels import LabelState
from tests.test_email_mvp_acceptance import create_reply, email_daemon, review, summarize


async def test_review_recovery_expires_consent_but_keeps_exact_history(tmp_path):
    kwargs = {"id_store_path": tmp_path / "ids.db", "review_store_path": tmp_path / "reviews.db"}
    queue = ApprovalQueue(**kwargs)
    pending = await queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        target="someone@example.test",
        payload="exact content",
        labels_in=LabelState(),
        justification="review",
    )
    denied = await queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        target="someone@example.test",
        payload="declined content",
        labels_in=LabelState(),
        justification="review",
    )
    await queue.deny(denied.id)
    recovered = ApprovalQueue(**kwargs)
    assert recovered.get(pending.id).status == ApprovalStatus.EXPIRED
    assert recovered.get(pending.id).payload == "exact content"
    assert recovered.get(pending.id).decided_by == "daemon-restart"
    assert recovered.get(denied.id).status == ApprovalStatus.DENIED
    assert (tmp_path / "reviews.db").stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError, match="not pending"):
        await recovered.approve(pending.id)


async def test_begin_edit_withdraws_every_old_review_before_editor_returns(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        original = await review(client, session_id, draft)
        reopened = await review(client, session_id, draft)
        assert reopened["id"] == original["id"]
        assert reopened["expires_at"] == original["expires_at"]
        editor = await client.call(
            "email.draft_begin_edit",
            {"session_id": session_id, "id": draft["id"], "version": draft["version"]},
        )
        assert editor["draft"]["body"] == draft["body"]
        assert app.approval_queue.get(original["id"]).status == ApprovalStatus.SUPERSEDED
        with pytest.raises(DaemonError, match="changed"):
            await client.call("approval.approve", {"id": original["id"]})
        fresh = await review(client, session_id, draft)
        assert fresh["id"] != original["id"]
        assert transport.attempts == []


async def test_task_projection_joins_exact_content_and_durable_effect(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        pending = await review(client, session_id, draft)
        before = await client.call("approval.task_list", {"session_id": session_id})
        row = before["reviews"][0]
        assert row["payload"] == pending["payload"]
        assert row["can_approve"] is True
        await client.call("approval.approve", {"id": pending["id"]})
        after = await client.call("approval.task_list", {"session_id": session_id})
        receipt = after["reviews"][0]
        assert receipt["review_state"] == "smtp_accepted"
        assert receipt["can_approve"] is False
        assert receipt["payload"] == pending["payload"]
        assert app.email_operations is not None
        assert receipt["operation"]["id"] == str(app.email_operations.all(UUID(session_id))[0].id)
        with pytest.raises(DaemonError, match=r"submitted|uncertain|not found"):
            await client.call(
                "email.draft_begin_edit",
                {"session_id": session_id, "id": draft["id"], "version": draft["version"]},
            )
        assert len(transport.attempts) == 1


async def test_task_projection_and_edit_cannot_cross_sessions(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, _app, _paths, transport):
        owner, _ = await summarize(client)
        draft = await create_reply(client, owner)
        await review(client, owner, draft)
        other = await client.call("session.new", {"owner": "test", "purpose_handle": "inbox"})
        assert (await client.call("approval.task_list", {"session_id": other["id"]}))[
            "reviews"
        ] == []
        with pytest.raises(DaemonError, match="not found"):
            await client.call(
                "email.draft_begin_edit",
                {"session_id": other["id"], "id": draft["id"], "version": draft["version"]},
            )
        assert transport.attempts == []


def test_review_mutation_and_protected_projection_require_operator():
    assert requires_operator("email.draft_begin_edit")
    assert requires_operator("approval.task_list")


async def test_competing_decisions_submit_only_once(tmp_path, monkeypatch):
    import asyncio

    from capabledeputy.daemon.approval_handlers import make_approval_handlers

    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        pending = await review(client, session_id, draft)
        approve = make_approval_handlers(app)["approval.approve"]
        first, second = await asyncio.gather(
            approve({"id": pending["id"]}), approve({"id": pending["id"]})
        )
        assert first["dispatch"]["output"]["status"] == "smtp_accepted"
        assert second["already_decided"] is True
        assert len(transport.attempts) == 1


async def test_stop_before_journal_claim_offers_fresh_review_without_replay(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        pending = await review(client, session_id, draft)

        async def stop(*args, **kwargs):
            raise ValueError("Stopped before dispatch")

        monkeypatch.setattr(
            "capabledeputy.daemon.approval_handlers._execute_declassified_email", stop
        )
        with pytest.raises(DaemonError, match="Stopped before dispatch"):
            await client.call("approval.approve", {"id": pending["id"]})
        projection = await client.call("approval.task_list", {"session_id": session_id})
        assert projection["reviews"][0]["review_state"] == "failed_before_submission"
        await client.call(
            "email.draft_begin_edit",
            {"session_id": session_id, "id": draft["id"], "version": draft["version"]},
        )
        assert transport.attempts == []
        assert app.approval_queue.get(pending["id"]).status == ApprovalStatus.APPROVED


async def test_restart_after_approval_without_evidence_requires_reconciliation(
    tmp_path, monkeypatch
):
    from capabledeputy.app import App

    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, _transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        pending = await review(client, session_id, draft)
        await app.approval_queue.approve(pending["id"])
        db = app._email_state_db_path
        audit_path = tmp_path / "recovered-audit.jsonl"
    recovered = App(state_db_path=db, audit_log_path=audit_path)
    await recovered.startup()
    try:
        record = recovered.approval_queue.get(pending["id"])
        assert record.status == ApprovalStatus.APPROVED
        assert recovered.email_operations is not None
        operation = recovered.email_operations.for_draft(UUID(draft["id"]), draft["version"])
        assert operation is not None and operation.status == "outcome_unknown"
        assert recovered.email_operations.blocks_draft(UUID(draft["id"]))
    finally:
        await recovered.shutdown()


async def test_group_counts_a_competing_denial_as_skipped(tmp_path, monkeypatch):
    from capabledeputy.app import App
    from capabledeputy.daemon.approval_handlers import make_approval_handlers
    from capabledeputy.daemon.settings_store import DaemonSettings

    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    session_id = uuid4()
    first, second = [
        await app.approval_queue.submit(
            from_session=session_id,
            action=ApprovalAction.DECLASSIFY,
            payload=payload,
            target="same target",
            labels_in=LabelState(),
        )
        for payload in ("first payload", "second payload")
    ]
    monkeypatch.setattr(
        "capabledeputy.daemon.approval_handlers.load_settings",
        lambda: DaemonSettings(require_touch_id_for_high_risk=True),
    )
    monkeypatch.setattr(
        "capabledeputy.daemon.approval_handlers._approval_requires_strong_auth", lambda _: True
    )
    handlers = make_approval_handlers(app)

    async def authenticate(request):
        assert request.id == first.id
        await handlers["approval.deny"]({"id": second.id})
        return {"method": "test"}

    monkeypatch.setattr(
        "capabledeputy.daemon.approval_handlers.authenticate_approval", authenticate
    )
    group = app.approval_queue.get(first.id).sibling_group_id
    result = await handlers["approval.approve_group"]({"group_id": str(group)})
    assert result["n_approved"] == 1
    assert result["n_skipped"] == 1
    assert result["n_failed"] == 0
    assert result["results"][1]["approval"]["status"] == "denied"
    assert result["results"][1]["skipped"] is True
