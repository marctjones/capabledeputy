"""Demo 04 — daily briefing through schema-validated extraction.

The user asks for a morning briefing. A sync job has previously written
`briefing.source` into the memory store with the day's calendar +
inbox content; the value is labeled `confidential.personal` and
`untrusted.external` because it has both kinds of content.

The agent doesn't read the raw briefing text. It calls
`quarantined.extract(key="briefing.source", schema="DailyBriefing")`
which runs a quarantined LLM with no tools and a Pydantic schema. The
planner sees ONLY the schema fields — not the raw labeled text.

That makes the briefing flow safe to compose with downstream actions
(e.g., emailing the structured briefing to yourself) without pulling
the labeled source into the planner's context.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label


async def test_daily_briefing_via_schema_extraction(tmp_path: Path) -> None:
    """The agent gets a structured DailyBriefing back from the
    quarantined LLM; the planner LLM never sees the raw briefing
    source. Verified by inspecting the planner LLM's recorded calls.
    """
    # Quarantined LLM produces a valid DailyBriefing JSON.
    quarantined = FakeLLMClient(
        [
            LLMResponse(
                content=(
                    '{"date": "2026-05-07", '
                    '"n_calendar_events": 3, '
                    '"n_unread_emails": 5, '
                    '"top_priority": "1:1 with Maria at 10am", '
                    '"suggested_focus": "ship the migration; '
                    "address contractor invoice"
                    '"}'
                ),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    # Planner LLM: calls quarantined.extract, then produces a final answer.
    planner = FakeLLMClient(
        [
            LLMResponse(
                content="Fetching today's briefing.",
                tool_calls=(
                    ToolCall(
                        id="e1",
                        name="quarantined.extract",
                        args={"key": "briefing.source", "schema": "DailyBriefing"},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content=(
                    "Today: 3 events, 5 unread. Top priority is your 1:1 with "
                    "Maria at 10am. Focus: ship the migration; address the "
                    "contractor invoice."
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

    # Pre-populated by an out-of-band sync job — the agent never reads
    # this text directly. Note the labels: PERSONAL (calendar) +
    # UNTRUSTED_EXTERNAL (inbox content).
    raw_source = (
        "CALENDAR\n"
        "  09:00 standup\n"
        "  10:00 1:1 with Maria\n"
        "  14:00 design review\n"
        "INBOX (5 unread)\n"
        "  alice@example.com — proposal review (high)\n"
        "  contractor@example.com — invoice #482\n"
        "  ...\n"
    )
    app.memory.write(
        "briefing.source",
        raw_source,
        frozenset({Label.CONFIDENTIAL_PERSONAL, Label.UNTRUSTED_EXTERNAL}),
    )

    s = await app.graph.new(intent="daily-briefing demo")
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "Give me my daily briefing."},
    )

    assert result["iterations"] == 2
    [extract_outcome] = result["tool_outcomes"]
    assert extract_outcome["decision"] == "allow"
    assert extract_outcome["output"]["schema"] == "DailyBriefing"
    data = extract_outcome["output"]["data"]
    assert data["n_calendar_events"] == 3
    assert data["n_unread_emails"] == 5

    # Critical security check — the planner LLM's recorded conversation
    # must NOT contain the raw briefing source. The quarantined extractor
    # returns NO additional_labels precisely because the schema is the
    # declassifier; if the planner had seen the raw text, it would have
    # been delivered as a tool message in `planner.calls[1]`.
    second_turn_messages = planner.calls[1][0]
    serialized = " ".join(m.content for m in second_turn_messages)
    assert "1:1 with Maria" in serialized  # came in via the schema field
    assert "alice@example.com" not in serialized  # did NOT leak
    assert "contractor@example.com" not in serialized  # did NOT leak
    assert "CALENDAR" not in serialized  # raw block didn't leak

    # The session's label state — the extract path doesn't propagate
    # the source's labels because the schema is the declassification.
    final = app.graph.get(UUID(str(s.id)))
    assert Label.CONFIDENTIAL_PERSONAL not in final.label_set
    assert Label.UNTRUSTED_EXTERNAL not in final.label_set


async def test_daily_briefing_naive_path_blocks_egress(tmp_path: Path) -> None:
    """Counter-example: the *naive* path — read inbox + calendar
    directly, then try to email the briefing — must be blocked.
    Demonstrates why the schema-extraction path is the right idiom.
    """
    planner = FakeLLMClient(
        [
            LLMResponse(
                content="Reading inbox.",
                tool_calls=(ToolCall(id="i1", name="inbox.list", args={}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content="Now emailing the briefing to myself.",
                tool_calls=(
                    ToolCall(
                        id="e1",
                        name="email.send",
                        args={
                            "to": "me@example.com",
                            "subject": "Briefing",
                            "body": "you have unread mail",
                        },
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content="Couldn't email — untrusted content meets egress.",
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=planner,
    )
    await app.startup()
    s = await app.graph.new(intent="naive briefing")
    caps = frozenset(
        {
            Capability(kind=CapabilityKind.READ_FS, pattern="*"),
            Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com"),
        },
    )
    app.graph._sessions[s.id] = replace(s, capability_set=caps)

    from datetime import UTC, datetime

    from capabledeputy.tools.native.inbox import InboundMessage

    app.inbox.add(
        InboundMessage(
            id="m1",
            sender="alice@example.com",
            subject="hi",
            body="hello",
            received_at=datetime.now(UTC),
        ),
    )

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "Read inbox then email me."},
    )
    decisions = [o["decision"] for o in result["tool_outcomes"]]
    rules = [o["rule"] for o in result["tool_outcomes"] if o["rule"]]
    assert "deny" in decisions
    assert "untrusted-meets-egress" in rules
