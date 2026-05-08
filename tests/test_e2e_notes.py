"""Demo 08 — note-taking with labeled memory.

Notes live in the labeled memory store. Each note carries the session's
labels at write time; reading propagates them back into the reading
session. That's how compartments stay separate: a session that's only
read personal notes never gains health labels, even if both kinds of
notes coexist in the same store.

Workflow:
  1. Write a personal note from a session with personal-only labels.
  2. Write a health note from a session with health-context labels.
  3. From a fresh session, read each note in turn and watch the label
     set grow accordingly.
  4. From a "personal-only" session, demonstrate that an attempt to
     email the personal note to a contact succeeds while the same
     attempt with a health note in scope is blocked.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label


async def test_notes_compartments_stay_separate(tmp_path: Path) -> None:
    """Reading a personal note doesn't taint a session with health
    labels even when health notes exist in the same store."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()

    # Write notes with different labels (the user does this through
    # their own labeled sessions in practice).
    app.memory.write(
        "notes.grocery",
        "milk, eggs, bread",
        frozenset({Label.CONFIDENTIAL_PERSONAL}),
    )
    app.memory.write(
        "notes.lab-results",
        "BP 120/80; LDL 110",
        frozenset({Label.CONFIDENTIAL_HEALTH}),
    )

    # Personal session: grocery list lookup.
    personal = await app.graph.new(intent="personal session")
    cap_read = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[personal.id] = replace(
        personal,
        capability_set=frozenset({cap_read}),
    )

    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content="Looking up grocery list.",
                tool_calls=(
                    ToolCall(id="r1", name="memory.read", args={"key": "notes.grocery"}),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="milk, eggs, bread.", finish_reason=FinishReason.STOP),
        ],
    )

    handlers = make_agent_handlers(app)
    await handlers["session.send"](
        {"session_id": str(personal.id), "message": "What's on my grocery list?"},
    )
    final_personal = app.graph.get(personal.id)
    assert Label.CONFIDENTIAL_PERSONAL in final_personal.label_set
    assert Label.CONFIDENTIAL_HEALTH not in final_personal.label_set

    # Health session: lab-results lookup. Separate session, separate
    # compartment. Health labels apply only here.
    health = await app.graph.new(intent="health session")
    app.graph._sessions[health.id] = replace(
        health,
        capability_set=frozenset({cap_read}),
    )
    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content="Looking up lab results.",
                tool_calls=(
                    ToolCall(
                        id="r1",
                        name="memory.read",
                        args={"key": "notes.lab-results"},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="BP 120/80, LDL 110.", finish_reason=FinishReason.STOP),
        ],
    )
    await handlers["session.send"](
        {"session_id": str(health.id), "message": "Read my lab results."},
    )
    final_health = app.graph.get(health.id)
    assert Label.CONFIDENTIAL_HEALTH in final_health.label_set
    # Critically: this session NEVER gained the personal label even
    # though both notes share the same memory store.
    assert Label.CONFIDENTIAL_PERSONAL not in final_health.label_set


async def test_notes_personal_egress_works_health_egress_blocks(
    tmp_path: Path,
) -> None:
    """Personal-only session can email; health-tainted session cannot."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()

    app.memory.write(
        "notes.book-rec",
        "you should read 'Designing Data-Intensive Applications'",
        frozenset({Label.CONFIDENTIAL_PERSONAL}),
    )
    app.memory.write(
        "notes.med-list",
        "lisinopril 10mg daily",
        frozenset({Label.CONFIDENTIAL_HEALTH}),
    )

    caps = frozenset(
        {
            Capability(kind=CapabilityKind.READ_FS, pattern="*"),
            Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com"),
        },
    )

    # Personal note + email egress: allowed (no conflict rule fires).
    personal = await app.graph.new(intent="personal egress")
    app.graph._sessions[personal.id] = replace(personal, capability_set=caps)
    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content="Reading the recommendation.",
                tool_calls=(
                    ToolCall(id="r1", name="memory.read", args={"key": "notes.book-rec"}),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content="Sending it.",
                tool_calls=(
                    ToolCall(
                        id="e1",
                        name="email.send",
                        args={
                            "to": "friend@example.com",
                            "subject": "book rec",
                            "body": "you should read DDIA",
                        },
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="sent.", finish_reason=FinishReason.STOP),
        ],
    )
    handlers = make_agent_handlers(app)
    pres = await handlers["session.send"](
        {
            "session_id": str(personal.id),
            "message": "Read book rec and email friend@example.com.",
        },
    )
    assert all(o["decision"] == "allow" for o in pres["tool_outcomes"])
    assert len(app.email_outbox.all()) == 1

    # Health note + email egress: blocked.
    health = await app.graph.new(intent="health egress")
    app.graph._sessions[health.id] = replace(health, capability_set=caps)
    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content="Reading med list.",
                tool_calls=(
                    ToolCall(id="r1", name="memory.read", args={"key": "notes.med-list"}),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content="Sending it.",
                tool_calls=(
                    ToolCall(
                        id="e1",
                        name="email.send",
                        args={
                            "to": "friend@example.com",
                            "subject": "meds",
                            "body": "lisinopril 10mg daily",
                        },
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="blocked.", finish_reason=FinishReason.STOP),
        ],
    )
    res = await handlers["session.send"](
        {
            "session_id": str(health.id),
            "message": "Read my meds and email friend@example.com.",
        },
    )
    decisions = [o["decision"] for o in res["tool_outcomes"]]
    assert "deny" in decisions
    # Outbox unchanged: only the personal-session send landed.
    assert len(app.email_outbox.all()) == 1
