"""End-to-end prescription-to-wife scenario (DESIGN.md §13 #1).

This is the canonical Phase 5 demo. A session reads PHI and is
correctly blocked from emailing. The user submits an approval
request with the verbatim summary they want sent to wife@example.com.
Approving the request spawns a one-shot session C, executes the
email send in C, and terminates C. The originating PHI session
NEVER gains the egress capability.

Demonstrates: approval queue, cross-session declassification,
purpose-limited sessions, audit trail across the entire flow.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.session.model import SessionStatus


@pytest.fixture
async def app(tmp_path: Path) -> App:
    return App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient(
            [
                LLMResponse(
                    content="reading prescription details",
                    tool_calls=(ToolCall(id="r1", name="memory.read", args={"key": "rx"}),),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(
                    content="attempting to email summary",
                    tool_calls=(
                        ToolCall(
                            id="e1",
                            name="email.send",
                            args={
                                "to": "wife@example.com",
                                "subject": "Your prescription",
                                "body": "Lisinopril 10mg daily",
                            },
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(
                    content=(
                        "I cannot email prescription details directly: the "
                        "session carries confidential.health and the "
                        "health-meets-egress rule blocks sends. Please "
                        "approve via capdep approval if you'd like me to "
                        "share the summary with your wife."
                    ),
                    finish_reason=FinishReason.STOP,
                ),
            ],
        ),
    )


async def test_prescription_to_wife_full_flow(app: App, tmp_path: Path) -> None:
    await app.startup()

    app.memory.write(
        "rx",
        "Lisinopril 10mg daily, recheck in 6 weeks",
        frozenset({Label.CONFIDENTIAL_HEALTH}),
    )

    health_session = await app.graph.new(intent="health-context")
    read_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    email_cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    app.graph._sessions[health_session.id] = replace(
        health_session,
        capability_set=frozenset({read_cap, email_cap}),
    )

    agent = make_agent_handlers(app)
    result = await agent["session.send"](
        {
            "session_id": str(health_session.id),
            "message": "Read my prescription and email a summary to wife@example.com.",
        },
    )

    assert any(o["decision"] == "deny" for o in result["tool_outcomes"])
    deny_outcome = next(o for o in result["tool_outcomes"] if o["decision"] == "deny")
    assert deny_outcome["rule"] == "health-meets-egress"
    assert len(app.email_outbox.all()) == 0

    health_state = app.graph.get(health_session.id)
    assert Label.CONFIDENTIAL_HEALTH in health_state.label_set

    approvals = make_approval_handlers(app)
    submitted = await approvals["approval.submit"](
        {
            "from_session": str(health_session.id),
            "action": "SEND_EMAIL",
            "payload": "Updated prescription: Lisinopril 10mg daily, recheck in 6 weeks.",
            "target": "wife@example.com",
            "labels_in": ["confidential.health"],
            "justification": "user wants spouse informed of new dose",
        },
    )
    assert submitted["status"] == "pending"

    pending = await approvals["approval.list"]({"status": "pending"})
    assert len(pending["approvals"]) == 1

    approve_result = await approvals["approval.approve"](
        {"id": submitted["id"], "decided_by": "marc"},
    )
    assert approve_result["approval"]["status"] == "approved"
    assert approve_result["dispatch"]["decision"] == "allow"
    assert "sent" in approve_result["dispatch"]["output"]

    assert len(app.email_outbox.all()) == 1
    sent = app.email_outbox.all()[0]
    assert sent.to == "wife@example.com"
    assert "Lisinopril" in sent.body

    after_health = app.graph.get(health_session.id)
    assert Label.CONFIDENTIAL_HEALTH in after_health.label_set
    assert all(
        c.kind != CapabilityKind.SEND_EMAIL or c.pattern != "wife@example.com"
        for c in after_health.capability_set
    )

    purpose_session_id = approve_result["executed_in_session"]
    from uuid import UUID

    purpose = app.graph.get(UUID(purpose_session_id))
    assert purpose.status == SessionStatus.ABORTED
    assert Label.CONFIDENTIAL_HEALTH not in purpose.label_set
    assert Label.TRUSTED_USER_DIRECT in purpose.label_set

    events = await app.audit.read_all()
    types = [e.event_type.value for e in events]
    assert "approval.requested" in types
    assert "approval.approved" in types
    assert "session.created" in types

    purpose_email_dispatches = [
        e
        for e in events
        if e.event_type.value == "tool.dispatched"
        and e.session_id == UUID(purpose_session_id)
        and e.payload.get("tool") == "email.send"
    ]
    assert len(purpose_email_dispatches) == 1


async def test_approval_denied_does_not_send(app: App) -> None:
    await app.startup()
    health_session = await app.graph.new(intent="health-context-2")

    approvals = make_approval_handlers(app)
    submitted = await approvals["approval.submit"](
        {
            "from_session": str(health_session.id),
            "action": "SEND_EMAIL",
            "payload": "anything",
            "target": "spam@example.com",
            "labels_in": ["confidential.health"],
            "justification": "test",
        },
    )

    denied = await approvals["approval.deny"](
        {"id": submitted["id"], "reason": "no thanks"},
    )
    assert denied["status"] == "denied"
    assert len(app.email_outbox.all()) == 0
