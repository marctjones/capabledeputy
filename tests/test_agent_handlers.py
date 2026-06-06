from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier


@pytest.fixture
async def app(tmp_path: Path) -> App:
    return App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([LLMResponse(content="ok")]),
    )


async def test_session_send_runs_agent_loop(app: App) -> None:
    await app.startup()
    s = await app.graph.new()

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "hi"},
    )
    assert result["content"] == "ok"
    assert result["iterations"] == 1


async def test_session_send_no_llm_raises(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=None,
    )
    await app.startup()
    s = await app.graph.new()
    handlers = make_agent_handlers(app)

    with pytest.raises(RuntimeError, match="no LLM client"):
        await handlers["session.send"]({"session_id": str(s.id), "message": "hi"})


async def test_session_grant_capability_persists(app: App) -> None:
    await app.startup()
    s = await app.graph.new()
    handlers = make_agent_handlers(app)

    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    result = await handlers["session.grant_capability"](
        {"session_id": str(s.id), "capability": cap.to_dict()},
    )
    assert any(c["pattern"] == "/home/*" for c in result["capability_set"])


async def test_session_send_returns_tool_outcomes(app: App) -> None:
    await app.startup()
    fake = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(
                    ToolCall(id="c1", name="memory.write", args={"key": "k", "value": "v"}),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="done", finish_reason=FinishReason.STOP),
        ],
    )
    app.llm_client = fake

    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "store"},
    )
    assert result["iterations"] == 2
    assert len(result["tool_outcomes"]) == 1
    assert result["tool_outcomes"][0]["decision"] == "allow"


async def test_session_send_label_propagation_visible_in_outcome(app: App) -> None:
    await app.startup()
    app.memory.write(
        "labs",
        "x",
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )

    fake = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(ToolCall(id="c", name="memory.read", args={"key": "labs"}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="read", finish_reason=FinishReason.STOP),
        ],
    )
    app.llm_client = fake
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    await handlers["session.send"](
        {"session_id": str(s.id), "message": "x"},
    )
    # Verify label propagation: after reading labeled data, the session's
    # label_state should contain the health category
    updated = app.graph.get(s.id)
    health_tag = next(
        (t for t in updated.label_state.a if t.category == "health"),
        None,
    )
    assert health_tag is not None, "health category should be propagated to session"


async def test_session_send_outcome_includes_tool_name_and_args(app: App) -> None:
    """The REPL and CLI need tool_name + tool_args on each outcome to
    auto-submit approvals and to render `allow inbox.read` instead of
    just `allow`."""
    await app.startup()
    app.memory.write(
        "labs",
        "x",
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )

    fake = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(ToolCall(id="c", name="memory.read", args={"key": "labs"}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="read", finish_reason=FinishReason.STOP),
        ],
    )
    app.llm_client = fake
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "x"},
    )
    [outcome] = result["tool_outcomes"]
    assert outcome["tool_name"] == "memory.read"
    assert outcome["tool_args"] == {"key": "labs"}


# --- Issue #23 — session.cancel RPC ---------------------------------------


async def test_session_cancel_no_active_turn_returns_false(app: App) -> None:
    """Cancelling a session with no in-flight turn is a no-op, not an
    error. The REPL fires cancel on every Ctrl-C without tracking
    whether a turn is actually running."""
    await app.startup()
    s = await app.graph.new()
    handlers = make_agent_handlers(app)

    result = await handlers["session.cancel"]({"session_id": str(s.id)})
    assert result == {"cancelled": False, "reason": "no active turn"}


async def test_session_cancel_flips_active_flag(app: App) -> None:
    """When session.send has registered a cancellation flag (i.e. a
    turn is in flight), session.cancel flips it True. The agent loop
    polls the flag and surfaces TurnInterrupted on the next iteration."""
    import uuid

    await app.startup()
    s = await app.graph.new()
    handlers = make_agent_handlers(app)

    # Simulate an in-flight turn by registering the flag manually.
    app.cancellation_flags[s.id] = False

    result = await handlers["session.cancel"]({"session_id": str(s.id)})
    assert result == {"cancelled": True}
    assert app.cancellation_flags[s.id] is True

    # Cancelling an unrelated session id returns the not-active form,
    # not an error — exercising the dict-key check.
    other_sid = uuid.uuid4()
    result2 = await handlers["session.cancel"]({"session_id": str(other_sid)})
    assert result2["cancelled"] is False


async def test_session_send_cancel_during_turn_interrupts(app: App) -> None:
    """End-to-end: a mid-turn cancel produces a graceful return with
    finish_reason=length and a `[turn cancelled by user]` content
    marker. The cancellation flag is cleaned up afterwards so a
    subsequent turn isn't pre-cancelled."""
    await app.startup()

    # FakeLLMClient with a tool call that triggers a second iteration
    # — gives us a window between iterations for the cancel to fire.
    fake = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(
                    ToolCall(id="c", name="memory.write", args={"key": "k", "value": "v"}),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="should-not-reach", finish_reason=FinishReason.STOP),
        ],
    )
    app.llm_client = fake
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)

    # Pre-set the cancellation flag so iteration #1's preflight check
    # trips immediately. (In real use, the cancel RPC fires
    # concurrently with session.send; here we shortcut to a
    # deterministic test by setting the flag before send_message
    # registers its own zero-init flag.)
    #
    # Trick: shim a cancellation-watching handler. session_send
    # initializes the flag to False — so we instead patch
    # cancellation_flags via a custom-flag pattern: the cancel_check
    # closure reads app.cancellation_flags[sid]; we flip it through
    # the canonical session.cancel RPC after session_send registers
    # its flag but before any LLM call resolves. Use an in-process
    # taskgroup to race them.
    import anyio

    async def _cancel_soon() -> None:
        # Yield so session_send registers the flag first.
        await anyio.sleep(0)
        await handlers["session.cancel"]({"session_id": str(s.id)})

    async def _send() -> dict:
        return await handlers["session.send"](
            {"session_id": str(s.id), "message": "go"},
        )

    result_holder: dict = {}

    async def _send_and_capture() -> None:
        result_holder["result"] = await _send()

    async with anyio.create_task_group() as tg:
        tg.start_soon(_send_and_capture)
        tg.start_soon(_cancel_soon)

    result = result_holder["result"]
    assert result["finish_reason"] == "length"
    assert "cancelled" in result["content"].lower()
    # Flag was cleared after the turn returned — next turn is not
    # pre-cancelled.
    assert s.id not in app.cancellation_flags
