"""Supervised mail regression evidence. All SMTP is fake; no account/network use."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalStatus
from capabledeputy.daemon.approval_handlers import _email_args, make_approval_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import CategoryTag, LabelState, tags_for_labels_strings
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.client import _audit_output_metadata
from capabledeputy.tools.native.email import DraftBox
from capabledeputy.tools.native.email_delivery import DeliveryResult
from capabledeputy.tools.registry import ToolDefinition, ToolResult


def test_drafts_survive_restart_with_owner_labels_and_version(tmp_path: Path):
    path = tmp_path / "state.db"
    labels = tags_for_labels_strings(frozenset({"confidential.personal", "untrusted.user_input"}))
    box = DraftBox(path)
    draft = box.save(
        session_id=uuid4(),
        to="self@example.com",
        subject="Re: hello",
        body="Reply",
        label_state=labels,
    )
    box.update(draft.id, body="Edited reply")
    recovered = DraftBox(path).get(draft.id)
    assert recovered is not None
    assert recovered.body == "Edited reply" and recovered.version == 2
    assert recovered.session_id == draft.session_id and recovered.label_state == labels


def test_operational_audit_does_not_copy_mail_content():
    result = _audit_output_metadata(
        {"body": "private", "summarized_count": 2, "complete": False, "ok": "private text"}
    )
    assert result["operation_status"] == {"summarized_count": 2, "complete": False}
    assert "private" not in json.dumps(result)


@pytest.fixture
async def app(tmp_path):
    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    await app.startup()
    yield app
    await app.shutdown()


async def _draft(app, labels=frozenset({"confidential.personal", "untrusted.user_input"})):
    session = await app.graph.new(intent="supervised mail", purpose_handle="inbox")
    await app.graph.grant_capability(
        session.id, Capability(kind=CapabilityKind.IMAP_READ, pattern="*")
    )
    await app.graph.add_tags(session.id, tags_for_labels_strings(labels))
    draft = app.email_drafts.save(
        session_id=session.id,
        to="self@example.com",
        subject="Re: Exact subject",
        body="Exact reviewed reply",
        label_state=app.graph.get(session.id).label_state,
    )
    return session, draft


async def test_review_requires_read_authority(app):
    session = await app.graph.new()
    handlers = make_approval_handlers(app)
    with pytest.raises(ValueError, match="not authorized"):
        await handlers["email.draft_list"]({"session_id": str(session.id)})


async def test_draft_discovery_is_session_scoped(app):
    owner, draft = await _draft(app)
    other, _ = await _draft(app)
    handlers = make_approval_handlers(app)
    result = await handlers["email.draft_list"]({"session_id": str(other.id)})
    assert str(draft.id) not in {d["id"] for d in result["drafts"]}
    assert app.graph.get(owner.id).label_state == draft.label_state


async def test_exact_reviewed_send_and_duplicate_click_do_not_resend(app, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "capabledeputy.tools.native.email.deliver",
        lambda **args: calls.append(args) or DeliveryResult(True, "fake-smtp", "accepted"),
    )
    session, draft = await _draft(app)
    handlers = make_approval_handlers(app)
    request = await handlers["email.draft_review"](
        {
            "session_id": str(session.id),
            "id": str(draft.id),
            "version": draft.version,
        }
    )
    assert calls == []  # requesting review never sends
    envelope = json.loads(request["payload"])
    assert envelope["message"]["subject"] == draft.subject
    result = await handlers["approval.approve"]({"id": request["id"], "decided_by": "test-human"})
    assert result["dispatch"]["output"]["delivered"] is True
    assert calls[0]["to"] == draft.to and calls[0]["subject"] == draft.subject
    assert calls[0]["body"] == draft.body
    assert app.email_drafts.get(draft.id) is None
    assert app.graph.get(session.id).label_state == draft.label_state
    duplicate = await handlers["approval.approve"]({"id": request["id"]})
    assert duplicate["already_decided"] and len(calls) == 1


async def test_edited_draft_invalidates_pending_review(app):
    session, draft = await _draft(app)
    handlers = make_approval_handlers(app)
    request = await handlers["email.draft_review"](
        {
            "session_id": str(session.id),
            "id": str(draft.id),
            "version": draft.version,
        }
    )
    app.email_drafts.update(draft.id, body="Different content")
    with pytest.raises(ValueError, match="changed"):
        await handlers["approval.approve"]({"id": request["id"]})
    assert app.approval_queue.get(request["id"]).status == ApprovalStatus.PENDING


async def test_failed_delivery_preserves_draft(app, monkeypatch):
    monkeypatch.setattr(
        "capabledeputy.tools.native.email.deliver",
        lambda **args: DeliveryResult(False, "fake-smtp", "connection failed"),
    )
    session, draft = await _draft(app)
    handlers = make_approval_handlers(app)
    request = await handlers["email.draft_review"](
        {
            "session_id": str(session.id),
            "id": str(draft.id),
            "version": draft.version,
        }
    )
    result = await handlers["approval.approve"]({"id": request["id"]})
    assert result["dispatch"]["output"]["delivered"] is False
    assert app.email_drafts.get(draft.id) == draft


async def test_personal_mail_review_does_not_release_other_categories(app):
    session, draft = await _draft(app, frozenset({"confidential.business"}))
    await app.graph.add_tags(
        session.id, LabelState(a=frozenset({CategoryTag("business", Tier.REGULATED)}))
    )
    with pytest.raises(ValueError, match="category-specific"):
        await make_approval_handlers(app)["email.draft_review"](
            {
                "session_id": str(session.id),
                "id": str(draft.id),
                "version": draft.version,
            }
        )


def test_exact_envelope_rejects_recipient_change_and_unreviewed_fields():
    def envelope(message):
        return json.dumps({"format": "capdep.email.v1", "message": message})

    with pytest.raises(ValueError, match="recipient"):
        _email_args(
            envelope({"to": "other@example.com", "subject": "s", "body": "b"}), "self@example.com"
        )
    with pytest.raises(ValueError, match="exactly"):
        _email_args(
            envelope({"to": "self@example.com", "subject": "s", "body": "b", "bcc": "other"}),
            "self@example.com",
        )


async def test_real_mail_projection_preserves_ids_labels_and_toolless_extraction(tmp_path):
    extractor = FakeLLMClient(
        [
            LLMResponse(
                content=json.dumps(
                    {
                        "sender": "Alice",
                        "subject": "Meeting",
                        "urgency": "low",
                        "one_line_summary": "Meeting tomorrow.",
                    }
                ),
                finish_reason=FinishReason.STOP,
            )
        ]
    )
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        quarantined_llm=extractor,
    )
    await app.startup()
    labels = tags_for_labels_strings(frozenset({"confidential.personal", "untrusted.user_input"}))
    calls = []

    async def read(args, context):
        calls.append(args)
        return ToolResult(
            output={
                "ok": True,
                "complete": True,
                "matched_count": 1,
                "messages": [
                    {
                        "found": True,
                        "uid": "104567",
                        "folder": "INBOX",
                        "uidvalidity": "123",
                        "body_present": True,
                        "body": "Private actual message body",
                    }
                ],
            }
        )

    app.registry.register(
        ToolDefinition(
            name="account.imap.read_recent",
            description="real read",
            capability_kind=CapabilityKind.IMAP_READ,
            handler=read,
            inherent_tags=labels,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-PII-DISCLOSURE",),
        )
    )
    session = await app.graph.new()
    await app.graph.grant_capability(
        session.id, Capability(kind=CapabilityKind.IMAP_READ, pattern="*")
    )
    result = await app.tool_client.call_tool(
        session.id, "quarantined.summarize_mail", {"max_results": 1}
    )
    assert result.output is not None
    assert result.output["summarized_count"] == 1
    assert result.output["summaries"][0]["uid"] == "104567"
    assert "Private actual message body" not in json.dumps(result.output)
    assert extractor.calls[0][1] == []
    assert app.graph.get(session.id).label_state == labels
    assert calls == [{"max_results": 1, "folder": "INBOX"}]
    app.cancellation_flags[session.id] = True
    cancelled = await app.tool_client.call_tool(session.id, "quarantined.summarize_mail", {})
    assert cancelled.output is not None
    assert cancelled.output["error"] == "cancelled" and len(calls) == 1
    await app.shutdown()
