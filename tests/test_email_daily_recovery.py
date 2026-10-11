"""Daily-use recovery at real daemon handlers; all mail transport is synthetic."""

from uuid import UUID

import pytest

from capabledeputy.ipc.client import DaemonError
from tests.test_email_mvp_acceptance import (
    SyntheticExtractor,
    create_reply,
    email_daemon,
    review,
    summarize,
)


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


async def test_same_epoch_deleted_provider_mail_retains_snapshot_after_restart(
    tmp_path, monkeypatch
):
    """Provider deletion cannot silently erase previously captured labeled evidence."""
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        source_id = draft["source_id"]
        transport.empty_mailbox = True
        result = await client.call(
            "session.send",
            {
                "session_id": session_id,
                "message": "Summarize my recent emails.",
                "max_iterations": 4,
            },
        )
        output = next(
            r["output"]
            for r in result["tool_outcomes"]
            if r["tool_name"] == "quarantined.summarize_mail"
        )
        assert output["fetched_count"] == 0
        retained = app.email_sources.get(source_id, UUID(session_id))
        assert retained is not None and retained["body_present"] and not retained["stale"]
        assert transport.attempts == []
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _, transport):
        retained = app.email_sources.get(source_id, UUID(session_id))
        assert retained is not None and retained["message_id"] == draft["in_reply_to"]
        approval = await review(client, session_id, draft)
        assert approval["status"] == "pending"
        assert transport.attempts == []


async def test_revoked_provider_credentials_fail_read_then_explicit_recovery(tmp_path, monkeypatch):
    from dataclasses import replace

    from capabledeputy.tools.registry import ToolResult

    async with email_daemon(tmp_path, monkeypatch) as (client, app, _, transport):
        session_id, _ = await summarize(client)
        provider = app.registry.get("acceptance.imap.read_recent")
        assert isinstance(app.quarantined_llm, SyntheticExtractor)
        extractor_calls = len(app.quarantined_llm.calls)
        source_count = len(app.email_sources.all(UUID(session_id)))

        async def revoked(args, context):
            return ToolResult(output={"ok": False, "error": "authentication_failed"})

        app.registry.unregister_prefix(provider.name)
        app.registry.register(replace(provider, handler=revoked))
        result = await client.call(
            "session.send",
            {
                "session_id": session_id,
                "message": "Summarize my recent emails.",
                "max_iterations": 4,
            },
        )
        output = next(
            r["output"]
            for r in result["tool_outcomes"]
            if r["tool_name"] == "quarantined.summarize_mail"
        )
        assert not output["ok"]
        assert len(app.quarantined_llm.calls) == extractor_calls
        assert len(app.email_sources.all(UUID(session_id))) == source_count
        assert transport.attempts == []
        app.registry.unregister_prefix(provider.name)
        app.registry.register(provider)
        await summarize(client, session_id=session_id)
        assert len(app.email_sources.all(UUID(session_id))) == source_count + 2
        assert transport.attempts == []
