"""Independent real-handler checks for durable mail claims and authorization."""

import asyncio
import threading
from dataclasses import replace
from uuid import UUID

import pytest

from capabledeputy.ipc.client import DaemonClient, DaemonError
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.pipeline import DecisionFrame
from capabledeputy.policy.rules import Decision
from tests.test_email_mvp_acceptance import create_reply, email_daemon, review, summarize


async def test_concurrent_distinct_approvals_submit_once(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        first = await review(client, session_id, draft)
        request = app.approval_queue.get(first["id"])
        # Normal reopen reuses a review now. Explicitly construct a second
        # valid request to keep exercising distinct-request journal deduplication.
        second = (
            await app.approval_queue.submit(
                from_session=request.from_session,
                action=request.action,
                payload=request.payload,
                target=request.target,
                labels_in=request.labels_in,
                justification=request.justification,
                allow_patterns=False,
            )
        ).to_dict()
        entered, release = threading.Event(), threading.Event()

        def held_delivery(**args):
            entered.set()
            assert release.wait(10), "Test did not release SMTP"
            return transport.deliver(**args)

        monkeypatch.setattr("capabledeputy.tools.native.email.deliver", held_delivery)
        task = asyncio.create_task(client.call("approval.approve", {"id": first["id"]}))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            other = DaemonClient(paths.socket)
            response = await other.call("approval.approve", {"id": second["id"]})
            assert response["dispatch"]["output"]["status"] == "submitting"
            assert response["dispatch"]["output"]["already_submitted"]
        finally:
            release.set()
        result = await task
        assert result["dispatch"]["output"]["status"] == "smtp_accepted"
        assert len(transport.attempts) == 1
        assert app.email_operations is not None and len(app.email_operations.all()) == 1


async def test_changed_account_rejects_existing_review_before_actuation(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        approval = await review(client, session_id, draft)
        assert app._email_smtp_config is not None
        app._email_smtp_config = replace(
            app._email_smtp_config, from_addr="different@email-mvp.invalid"
        )
        with pytest.raises(DaemonError, match="account changed"):
            await client.call("approval.approve", {"id": approval["id"]})
        assert transport.attempts == []
        assert app.email_operations is not None and app.email_operations.all() == []


async def test_environment_drift_cannot_replace_explicit_reviewed_account(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, _app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        approval = await review(client, session_id, draft)
        monkeypatch.setenv("CAPDEP_SMTP_HOST", "changed.invalid")
        monkeypatch.setenv("CAPDEP_SMTP_FROM", "changed@other.invalid")
        result = await client.call("approval.approve", {"id": approval["id"]})
        assert result["dispatch"]["output"]["status"] == "smtp_accepted"
        assert transport.attempts[0]["from_addr"] == "owner@email-mvp.invalid"


async def test_policy_is_rechecked_after_human_approval(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        approval = await review(client, session_id, draft)
        original = app.tool_client._policy_pipeline
        attempts = []

        class DenySend:
            def decide(self, request):
                if request.action.kind == CapabilityKind.SEND_EMAIL:
                    attempts.append(request)
                    return DecisionFrame(
                        request, PolicyDecision(Decision.DENY, reason="Operator policy changed")
                    )
                return original.decide(request)

        monkeypatch.setattr(app.tool_client, "_policy_pipeline", DenySend())
        result = await client.call("approval.approve", {"id": approval["id"]})
        assert result["dispatch"]["decision"] == "deny"
        assert len(attempts) == 1 and attempts[0].already_approved is True
        assert transport.attempts == []
        assert app.email_operations is not None and app.email_operations.all() == []


async def test_edit_after_validation_is_rechecked_before_journal_claim(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        approval = await review(client, session_id, draft)
        original = app.graph.grant_capability

        async def edit_after_grant(session_id, capability, **kwargs):
            result = await original(session_id, capability, **kwargs)
            if capability.kind == CapabilityKind.SEND_EMAIL:
                app.email_drafts.update(UUID(draft["id"]), body="Concurrent content edit")
            return result

        monkeypatch.setattr(app.graph, "grant_capability", edit_after_grant)
        result = await client.call("approval.approve", {"id": approval["id"]})
        assert result["dispatch"]["error"]
        assert transport.attempts == []
        assert app.email_operations is not None and app.email_operations.all() == []


async def test_unknown_requires_explicit_reconciliation_then_new_review(tmp_path, monkeypatch):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        approval = await review(client, session_id, draft)
        transport.mode = "unknown"
        result = await client.call("approval.approve", {"id": approval["id"]})
        operation_id = result["dispatch"]["output"]["operation_id"]
        edit = {
            "session_id": session_id,
            "id": draft["id"],
            "version": draft["version"],
            "to": draft["to"],
            "subject": draft["subject"],
            "body": "Verified unsent, new revision",
        }
        with pytest.raises(DaemonError, match="submitted or uncertain"):
            await client.call("email.draft_update", edit)
        with pytest.raises(DaemonError, match=r"operation|submission"):
            await review(client, session_id, draft)
        reconcile = {
            "session_id": session_id,
            "id": operation_id,
            "outcome": "confirmed_not_sent",
            "reason": "Provider trace confirms no message was accepted",
        }
        with pytest.raises(DaemonError, match="confirmation"):
            await client.call("email.operation_reconcile", reconcile)
        with pytest.raises(DaemonError, match=r"(?i)operator|permission|trusted"):
            await DaemonClient(paths.socket, trusted=False).call(
                "email.operation_reconcile", {**reconcile, "confirmed": True}
            )
        receipt = await client.call("email.operation_reconcile", {**reconcile, "confirmed": True})
        assert receipt["operation"]["status"] == "outcome_unknown"
        assert receipt["operation"]["reconciliation_outcome"] == "confirmed_not_sent"
        with pytest.raises(DaemonError, match=r"operation|submission"):
            await review(client, session_id, draft)  # reconciliation never revives old version
        duplicate = await client.call("approval.approve", {"id": approval["id"]})
        assert duplicate["already_decided"] and len(transport.attempts) == 1
        newer = (await client.call("email.draft_update", edit))["draft"]
        assert newer["version"] > draft["version"]
        fresh_review = await review(client, session_id, newer)
        assert len(transport.attempts) == 1
        transport.mode = "accepted"
        await client.call("approval.approve", {"id": fresh_review["id"]})
        assert len(transport.attempts) == 2
        assert transport.attempts[0]["message_id"] != transport.attempts[1]["message_id"]
        assert app.email_operations is not None
        original = app.email_operations.get(UUID(operation_id))
        assert original is not None and original.status == "outcome_unknown"


async def test_unknown_blocks_review_even_if_draft_version_was_changed_outside_rpc(
    tmp_path, monkeypatch
):
    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        approval = await review(client, session_id, draft)
        transport.mode = "unknown"
        await client.call("approval.approve", {"id": approval["id"]})
        updated = app.email_drafts.update(UUID(draft["id"]), body="Attempt new version bypass")
        assert updated is not None
        with pytest.raises(DaemonError, match=r"operation|submission"):
            await review(client, session_id, {**draft, "version": updated.version})
        assert len(transport.attempts) == 1


@pytest.mark.parametrize("change", ["labels", "abort"])
async def test_source_state_changed_after_approval_validation_cannot_send(
    tmp_path, monkeypatch, change
):
    from capabledeputy.policy.labels import CategoryTag, LabelState
    from capabledeputy.policy.tiers import Tier

    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id)
        approval = await review(client, session_id, draft)
        original = app.graph.grant_capability

        async def change_after_grant(execution_id, capability, **kwargs):
            result = await original(execution_id, capability, **kwargs)
            if capability.kind == CapabilityKind.SEND_EMAIL:
                if change == "abort":
                    await app.graph.abort(UUID(session_id))
                else:
                    await app.graph.add_tags(
                        UUID(session_id),
                        LabelState(a=frozenset({CategoryTag("business", Tier.REGULATED)})),
                    )
            return result

        monkeypatch.setattr(app.graph, "grant_capability", change_after_grant)
        result = await client.call("approval.approve", {"id": approval["id"]})
        assert result["dispatch"]["error"]
        assert transport.attempts == []
        assert app.email_operations is not None and app.email_operations.all() == []
