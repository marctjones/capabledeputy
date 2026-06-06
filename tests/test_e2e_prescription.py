"""End-to-end prescription scenario (DESIGN.md §13 scenario #1).

A session with health and personal data plus several capabilities
attempts a multi-step task: read prescription data (which carries
confidential.health), then attempt to send via an egress tool. The
attempt is blocked at the correct point — health-meets-egress fires
on the second tool call after the first call's labels have propagated
into the session.

This is the Phase 4 done-when criterion in test form.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
)
from capabledeputy.policy.tiers import Tier


async def test_health_data_blocks_egress_attempt(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient(
            [
                LLMResponse(
                    content="reading lab results",
                    tool_calls=(ToolCall(id="r1", name="memory.read", args={"key": "labs"}),),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(
                    content="queueing pharmacy purchase",
                    tool_calls=(
                        ToolCall(
                            id="p1",
                            name="purchase.queue",
                            args={
                                "vendor": "pharmacy",
                                "item": "rx",
                                "amount": 50,
                            },
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(
                    content=(
                        "I cannot place this purchase: the session now carries "
                        "confidential health labels which conflict with egress "
                        "actions per the health-meets-egress rule."
                    ),
                    finish_reason=FinishReason.STOP,
                ),
            ],
        ),
    )
    await app.startup()

    app.memory.write(
        "labs",
        "BP=120/80, glucose=95, prescription: lisinopril 10mg",
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )

    s = await app.graph.new(intent="prescription review")
    read_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    purchase_cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=10_000,
    )
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({read_cap, purchase_cap}),
    )

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "review my labs and order my refill"},
    )

    assert result["iterations"] == 3
    assert len(result["tool_outcomes"]) == 2

    read_outcome, purchase_outcome = result["tool_outcomes"]
    assert read_outcome["decision"] == "allow"

    assert purchase_outcome["decision"] == "deny"
    assert purchase_outcome["rule"] == "health-meets-egress"
    assert "health" in (purchase_outcome["reason"] or "").lower()

    final_session = app.graph.get(s.id)
    assert any(tag.category == "health" for tag in final_session.label_state.a)
    assert len(final_session.history) == 2

    events = await app.audit.read_all()
    types = [e.event_type.value for e in events]
    assert "policy.decided" in types
    assert "tool.dispatched" in types
    assert "tool.returned" in types
    assert "label.propagated" in types
    assert "llm.request_sent" in types

    queue = app.purchase_queue.all()
    assert len(queue) == 0


async def test_clean_session_purchase_succeeds(tmp_path: Path) -> None:
    """Counterpart: without health labels in scope, an authorized purchase queues fine."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient(
            [
                LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(
                            id="p1",
                            name="purchase.queue",
                            args={
                                "vendor": "amazon",
                                "item": "book",
                                "amount": 25,
                            },
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(
                    content="purchase queued for your approval",
                    finish_reason=FinishReason.STOP,
                ),
            ],
        ),
    )
    await app.startup()

    s = await app.graph.new()
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=1_000,
    )
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "buy a book"},
    )

    assert result["tool_outcomes"][0]["decision"] == "allow"
    assert len(app.purchase_queue.all()) == 1
    queued = app.purchase_queue.all()[0]
    assert queued.vendor == "amazon"
    assert queued.amount == 25
