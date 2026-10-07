"""Daily-use recovery at real daemon handlers; all mail transport is synthetic."""

from uuid import UUID

import pytest

from capabledeputy.ipc.client import DaemonError
from tests.test_email_mvp_acceptance import create_reply, email_daemon, review, summarize


async def test_journal_claim_failure_prevents_smtp(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        approval = await review(client, session_id, draft)
        assert app.email_operations is not None

        def fail_claim(**kwargs):
            raise OSError("Synthetic storage unavailable")

        monkeypatch.setattr(app.email_operations, "claim", fail_claim)
        result = await client.call("approval.approve", {"id": approval["id"]})
        assert result["dispatch"]["error"]
        assert transport.attempts == []
        assert app.email_operations.all() == []


@pytest.mark.parametrize("all_writes_fail", [False, True])
async def test_status_storage_failure_after_smtp_never_replays(
    tmp_path, monkeypatch, all_writes_fail
):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        approval = await review(client, session_id, draft)
        journal = app.email_operations
        assert journal is not None
        original_finish = journal.finish

        def fail_finish(operation_id, status, detail=""):
            if all_writes_fail or status == "smtp_accepted":
                raise OSError("Synthetic status persistence unavailable")
            return original_finish(operation_id, status, detail)

        monkeypatch.setattr(journal, "finish", fail_finish)
        result = await client.call("approval.approve", {"id": approval["id"]})
        assert result["dispatch"]["error"]
        assert len(transport.attempts) == 1
        operation = journal.all()[0]
        operation_id = str(operation.id)
        assert operation.status == ("submitting" if all_writes_fail else "outcome_unknown")
        duplicate = await client.call("approval.approve", {"id": approval["id"]})
        assert duplicate["already_decided"]
        with pytest.raises(DaemonError, match=r"operation|submission"):
            await review(client, session_id, draft)
        assert len(transport.attempts) == 1
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _, transport):
        recovered = await client.call(
            "email.operation_status", {"session_id": session_id, "id": operation_id}
        )
        assert recovered["operation"]["status"] == "outcome_unknown"
        assert app.email_operations is not None
        restored = app.email_operations.get(UUID(operation_id))
        assert restored is not None and restored.message_id == operation.message_id
        with pytest.raises(DaemonError, match=r"operation|submission"):
            await review(client, session_id, draft)
        assert len(transport.attempts) == 1
