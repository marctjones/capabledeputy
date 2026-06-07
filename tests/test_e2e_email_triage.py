"""Demo 05 — email triage with schema-validated review and approval-gated reply.

The user wants to triage their inbox: see a summary of pending mail
and reply to one specific sender. The architecture stops the planner
from ever seeing raw inbound bodies; replies require explicit per-
recipient approval; auto-approval patterns can be wired in for
recurring correspondents.

Workflow:
  1. Each inbound message is stored in memory under
     `inbox.<id>` and labeled `untrusted.external`.
  2. Agent calls `quarantined.extract(key, schema=EmailTriageItem)`
     for each candidate; planner sees structured rows only.
  3. To reply, the user submits an explicit approval request with
     a verbatim payload + recipient. Cross-session declassification
     spawns a one-shot purpose session.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.tools.native.inbox import InboundMessage

_UNTRUSTED = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))


async def test_triage_via_schema_then_approval_gated_reply(tmp_path: Path) -> None:
    """Schema extraction lets the planner see triage metadata without
    ever loading the inbound body. Sending a reply requires explicit
    approval and runs in a purpose-limited session.
    """
    quarantined = FakeLLMClient(
        [
            LLMResponse(
                content=json.dumps(
                    {
                        "sender": "alice@example.com",
                        "subject": "Q2 proposal review",
                        "urgency": "high",
                        "one_line_summary": ("Asks for review by Friday on attached proposal"),
                    },
                ),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    planner = FakeLLMClient(
        [
            LLMResponse(
                content="Triaging the message from alice.",
                tool_calls=(
                    ToolCall(
                        id="t1",
                        name="quarantined.extract",
                        args={"key": "inbox.m1", "schema": "EmailTriageItem"},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content=(
                    "alice@example.com (high): Asks for review by Friday on "
                    "attached proposal. Suggest reviewing today."
                ),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=planner,
        quarantined_llm=quarantined,
    )
    await app.startup()

    inbound = InboundMessage(
        id="m1",
        sender="alice@example.com",
        subject="Q2 proposal review",
        body=(
            "Hi — please review the attached Q2 proposal by Friday. Marc "
            "asked you to drive this. Thanks!"
        ),
        received_at=datetime.now(UTC),
    )
    app.inbox.add(inbound)
    # The triage flow stages the inbound body in memory under a known
    # key, labeled untrusted.external. A real deployment would have a
    # daemon-side hook that does this on inbox arrival; for the demo
    # we do it inline.
    app.memory.write(
        "inbox.m1",
        inbound.body,
        _UNTRUSTED,
    )

    s = await app.graph.new(intent="triage demo")
    caps = frozenset(
        {
            Capability(kind=CapabilityKind.READ_FS, pattern="*"),
            Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com"),
        },
    )
    app.graph._sessions[s.id] = replace(s, capability_set=caps)

    agent = make_agent_handlers(app)
    triage = await agent["session.send"](
        {"session_id": str(s.id), "message": "Triage the message from alice."},
    )
    assert triage["tool_outcomes"][0]["decision"] == "allow"

    # Critical: planner never saw raw body content
    second_turn_msgs = planner.calls[1][0]
    serialized = " ".join(m.content for m in second_turn_msgs)
    assert "Marc asked you to drive this" not in serialized
    # ... but the schema fields did make it through:
    assert "high" in serialized

    # Now reply via the approval-gated SEND_EMAIL path. The user
    # provides the verbatim payload — no LLM paraphrase.
    approvals = make_approval_handlers(app)
    submit = await approvals["approval.submit"](
        {
            "from_session": str(s.id),
            "action": "SEND_EMAIL",
            "payload": "Reviewing today, will respond by Friday EOD. — m",
            "target": "alice@example.com",
            "labels_in": ["untrusted.external"],
            "justification": "reply to high-urgency proposal review",
        },
    )
    decision = await approvals["approval.approve"](
        {"id": submit["id"], "decided_by": "marc"},
    )
    assert decision["dispatch"]["decision"] == "allow"
    assert len(app.email_outbox.all()) == 1
    sent = app.email_outbox.all()[0]
    assert sent.to == "alice@example.com"
    assert "respond by Friday EOD" in sent.body

    # Original triage session is unchanged: no SEND_EMAIL capability
    # gained, raw inbound body never entered the planner LLM context.
    final = app.graph.get(s.id)
    assert all(
        c.kind != CapabilityKind.SEND_EMAIL or c.pattern != "alice@example.com"
        for c in final.capability_set
    )
