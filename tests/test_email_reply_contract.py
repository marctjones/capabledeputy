"""Source identity and edit contracts independent of any mail account."""

from uuid import uuid4

import pytest

from capabledeputy.policy.labels import tags_for_labels_strings
from capabledeputy.tools.native.email import DraftBox, EmailSourceBox, single_recipient


def test_source_snapshot_retains_body_headers_labels_and_session_after_restart(tmp_path):
    path = tmp_path / "sources.db"
    owner = uuid4()
    labels = tags_for_labels_strings(frozenset({"confidential.personal", "untrusted.external"}))
    source = EmailSourceBox(path).capture(
        session_id=owner,
        provider_tool="work.imap.read_recent",
        message={
            "folder": "INBOX",
            "uid": "42",
            "uidvalidity": "73",
            "from": "sender@example.com",
            "reply_to": "reply@example.com",
            "subject": "Subject",
            "message_id": "<source@example.com>",
            "references": "<older@example.com>",
            "body": "Source content",
            "body_present": True,
            "body_truncated": False,
        },
        labels=labels,
        account={
            "account_id": "work.imap",
            "from_addr": "owner@example.com",
            "account_fingerprint": "fingerprint",
        },
    )
    recovered = EmailSourceBox(path)
    assert recovered.get(source["id"], owner) == source
    assert recovered.get(source["id"], uuid4()) is None
    assert recovered.all(uuid4()) == []
    assert source["labels"] == labels.to_dict()
    assert source["body"] == "Source content"
    assert source["reply_to"] == "reply@example.com"


def test_version_checked_edit_preserves_source_and_refuses_lost_update(tmp_path):
    box = DraftBox(tmp_path / "drafts.db")
    source = {"source_id": "source", "in_reply_to": "<source@example.com>"}
    draft = box.save(
        session_id=uuid4(),
        to="to@example.com",
        subject="Re: Subject",
        body="Original",
        source=source,
    )
    revised = box.update(draft.id, expected_version=1, body="Revised")
    assert revised is not None
    assert revised.version == 2
    assert revised.source == source
    with pytest.raises(ValueError, match="changed"):
        box.update(draft.id, expected_version=1, body="Stale")
    recovered = box.get(draft.id)
    assert recovered is not None
    assert recovered.body == "Revised"


@pytest.mark.parametrize(
    "value",
    [
        "one@example.com, two@example.com",
        "one@example.com\r\nBcc: two@example.com",
        "not-an-address",
        "",
        "a@@example.com",
    ],
)
def test_reply_recipient_rejects_multiple_addresses_or_header_injection(value):
    with pytest.raises(ValueError, match="recipient"):
        single_recipient(value)


def test_reply_recipient_uses_authoritative_mailbox_not_display_name():
    assert single_recipient("Alice <reply@example.com>") == "reply@example.com"


def test_new_mailbox_epoch_invalidates_old_sources_across_restart(tmp_path):
    path = tmp_path / "sources.db"
    owner = uuid4()
    box = EmailSourceBox(path)
    source = box.capture(
        session_id=owner,
        provider_tool="mail.imap.read_recent",
        message={"folder": "INBOX", "uid": "42", "uidvalidity": "73"},
        labels=tags_for_labels_strings(frozenset({"confidential.personal"})),
        account={},
    )
    assert box.get(source["id"], owner) is not None
    # A later empty mailbox read still supplies its new epoch.
    box.observe_mailbox("mail.imap.read_recent", "INBOX", "74")
    recovered = EmailSourceBox(path)
    assert recovered.get(source["id"], owner) is None
    assert recovered.all(owner)[0]["stale"] is True


def test_mailbox_epoch_is_scoped_to_provider_and_folder():
    owner = uuid4()
    box = EmailSourceBox()
    source = box.capture(
        session_id=owner,
        provider_tool="mail.imap.read_recent",
        message={"folder": "INBOX", "uid": "42", "uidvalidity": "73"},
        labels=tags_for_labels_strings(frozenset()),
        account={},
    )
    box.observe_mailbox("other.imap.read_recent", "INBOX", "99")
    box.observe_mailbox("mail.imap.read_recent", "Archive", "99")
    assert box.get(source["id"], owner) is not None


def test_inbox_followup_retains_tool_surface_without_turning_greeting_into_a_read():
    from capabledeputy.agent.chat_turn import is_conversational_turn

    assert not is_conversational_turn("And the other one?", purpose_handle="inbox")
    assert is_conversational_turn("And the other one?")
    assert is_conversational_turn("Thanks!", purpose_handle="inbox")


async def test_general_chat_can_summarize_create_edit_and_review_a_local_reply(
    tmp_path, monkeypatch
):
    from uuid import UUID

    from capabledeputy.policy.capabilities import CapabilityKind
    from tests.test_email_mvp_acceptance import create_reply, email_daemon, review, summarize

    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        session = await client.call(
            "session.new", {"owner": "CapDepMac", "purpose_handle": "general"}
        )
        session_id = session["id"]
        await summarize(client, session_id=session_id)
        draft = await create_reply(client, session_id)
        edited = await client.call(
            "email.draft_update",
            {
                "session_id": session_id,
                "id": draft["id"],
                "version": draft["version"],
                "to": draft["to"],
                "subject": draft["subject"],
                "body": "Confirmed from general chat.",
            },
        )
        assert edited["draft"]["version"] == draft["version"] + 1
        approval = await review(client, session_id, edited["draft"])
        assert approval["status"] == "pending"
        assert transport.reads == 1
        assert transport.attempts == []
        source_session = app.graph.get(UUID(session_id))
        assert any(
            cap.kind == CapabilityKind.CREATE_FS and cap.pattern == "local-email-drafts://*"
            for cap in source_session.capability_set
        )
        assert not any(
            cap.kind == CapabilityKind.SEND_EMAIL for cap in source_session.capability_set
        )
        approved = await client.call("approval.approve", {"id": approval["id"]})
        assert approved["dispatch"]["output"]["status"] == "smtp_accepted"
        assert len(transport.attempts) == 1
        assert not any(
            cap.kind == CapabilityKind.SEND_EMAIL
            for cap in app.graph.get(UUID(session_id)).capability_set
        )


async def test_receipt_identity_is_exact_body_free_and_protected_by_owner_read_authority(
    tmp_path, monkeypatch
):
    import json
    from uuid import UUID

    from capabledeputy.ipc.client import DaemonError
    from capabledeputy.policy.capabilities import CapabilityKind
    from tests.test_email_mvp_acceptance import create_reply, email_daemon, review, summarize

    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, _transport):
        session_id, _ = await summarize(client)
        draft = await create_reply(client, session_id, body="Private receipt body stays protected")
        approval = await review(client, session_id, draft)
        frozen = json.loads(approval["payload"])["message"]
        await client.call("approval.approve", {"id": approval["id"]})
        receipts = await client.call("email.operation_list", {"session_id": session_id})
        receipt = receipts["operations"][0]
        identity_fields = (
            "subject",
            "to",
            "from_addr",
            "account_id",
            "in_reply_to",
            "references",
            "message_id",
        )
        assert {key: receipt[key] for key in identity_fields} == {
            key: frozen[key] for key in identity_fields
        }
        assert receipt["recipient"] == frozen["to"]
        assert "body" not in receipt and "Private receipt body" not in json.dumps(receipt)
        status = await client.call(
            "email.operation_status", {"session_id": session_id, "id": receipt["id"]}
        )
        assert status["operation"] == receipt
        other = await client.call("session.new", {"owner": "CapDepMac", "purpose_handle": "inbox"})
        assert (await client.call("email.operation_list", {"session_id": other["id"]}))[
            "operations"
        ] == []
        with pytest.raises(DaemonError, match="not found"):
            await client.call(
                "email.operation_status", {"session_id": other["id"], "id": receipt["id"]}
            )
        for cap in app.graph.get(UUID(session_id)).capability_set:
            if cap.kind == CapabilityKind.IMAP_READ:
                await app.graph.revoke_capability(UUID(session_id), cap.audit_id)
        with pytest.raises(DaemonError, match="not authorized"):
            await client.call(
                "email.operation_status", {"session_id": session_id, "id": receipt["id"]}
            )


@pytest.mark.parametrize(
    "prompt,neutral",
    [
        ("Summarize my recent emails.", True),
        ("Please list my two most recent messages", True),
        ("Summarize my recent emails since 2026-10-01", False),
        ("Summarize my recent emails from Alice", False),
        ("Summarize my recent emails in Archive", False),
        ("And yesterday?", False),
    ],
)
def test_recent_scope_guard_distinguishes_explicit_user_filters(prompt, neutral):
    from capabledeputy.session.model import Turn
    from capabledeputy.tools.native.mail_summary import neutral_recent_request

    assert neutral_recent_request((Turn(turn_id=1, role="user", content=prompt),)) is neutral


async def test_neutral_recent_request_cannot_acquire_model_invented_date_filter(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from uuid import UUID

    from capabledeputy.session.model import Turn
    from tests.test_email_mvp_acceptance import email_daemon

    async with email_daemon(tmp_path, monkeypatch) as (client, app, _paths, transport):
        calls = []
        provider = app.registry.get("acceptance.imap.read_recent")

        async def tracked_read(args, context):
            calls.append(dict(args))
            return await provider.handler(args, context)

        app.registry.unregister_prefix(provider.name)
        app.registry.register(replace(provider, handler=tracked_read))
        session = await client.call(
            "session.new", {"owner": "CapDepMac", "purpose_handle": "general"}
        )
        session_id = UUID(session["id"])
        await app.graph.add_turn(
            session_id, Turn(turn_id=1, role="user", content="Summarize my recent emails.")
        )
        result = await app.tool_client.call_tool(
            session_id,
            "quarantined.summarize_mail",
            {
                "since_date": "2023-10-01",
                "query": "invented",
                "folder": "Archive",
                "max_results": 2,
            },
        )
        assert result.output is not None
        assert result.output["scope"] == {
            "folder": "INBOX",
            "since_date": None,
            "query": None,
            "correction_applied": True,
        }
        assert result.output["summarized_count"] == 2
        assert transport.reads == 1
        assert transport.attempts == []
        assert calls == [{"max_results": 2, "folder": "INBOX"}]
        await app.graph.add_turn(
            session_id,
            Turn(turn_id=2, role="user", content="Summarize my recent emails since 2026-10-01."),
        )
        dated = await app.tool_client.call_tool(
            session_id, "quarantined.summarize_mail", {"since_date": "2026-10-01", "max_results": 2}
        )
        assert dated.output is not None
        assert dated.output["scope"]["correction_applied"] is False
        assert calls[-1] == {"max_results": 2, "folder": "INBOX", "since_date": "2026-10-01"}
