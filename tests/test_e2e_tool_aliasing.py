"""Demo 13 — tool-token aliasing (strict ocap).

When a session is created with --tool-tokens, the LLM-visible tool
names are deterministic per-session aliases (`t_<hash>`), not the
canonical names. The harness reverse-maps before dispatch. A token
from session A is meaningless in session B because the hash is
session-scoped — the LLM in session B doesn't know the token of any
tool in session A.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.tools.aliasing import alias_for


async def test_aliased_session_sees_random_token_names(tmp_path: Path) -> None:
    """The agent loop builds tool descriptions for the LLM with
    aliased names; the LLM picks the alias; the harness reverse-maps."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new(intent="aliased session", tool_aliasing=True)
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    app.memory.write("k", "v", frozenset())

    # The agent loop calls memory.read but the LLM is asked to use
    # the aliased name. The fake LLM is scripted to call the alias.
    aliased = alias_for(s.id, "memory.read")
    assert aliased.startswith("t_")
    assert aliased != "memory.read"

    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content="reading",
                tool_calls=(ToolCall(id="r1", name=aliased, args={"key": "k"}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="done", finish_reason=FinishReason.STOP),
        ],
    )
    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "read k"},
    )
    [outcome] = result["tool_outcomes"]
    assert outcome["decision"] == "allow"


async def test_token_from_one_session_does_not_dispatch_in_another(
    tmp_path: Path,
) -> None:
    """Cross-session token reuse fails. The token bound to session A
    is not bound to any tool in session B; dispatch returns the
    'tool not found' deny shape."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    a = await app.graph.new(tool_aliasing=True)
    b = await app.graph.new(tool_aliasing=True)
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[a.id] = replace(a, capability_set=frozenset({cap}))
    app.graph._sessions[b.id] = replace(b, capability_set=frozenset({cap}))
    app.memory.write("k", "v", frozenset())

    # Token bound to session A.
    a_token = alias_for(a.id, "memory.read")
    b_token = alias_for(b.id, "memory.read")
    assert a_token != b_token

    # Use A's token while running in B's session — should NOT match.
    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content="trying foreign token",
                tool_calls=(ToolCall(id="r1", name=a_token, args={"key": "k"}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="halted", finish_reason=FinishReason.STOP),
        ],
    )
    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(b.id), "message": "x"},
    )
    [outcome] = result["tool_outcomes"]
    assert outcome["decision"] == "deny"
    assert "not found" in (outcome["reason"] or "")


async def test_aliasing_off_uses_canonical_names(tmp_path: Path) -> None:
    """Default behaviour stays the same when aliasing isn't enabled."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new()  # tool_aliasing=False default
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    app.memory.write("k", "v", frozenset())

    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content="reading",
                tool_calls=(ToolCall(id="r1", name="memory.read", args={"key": "k"}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="done", finish_reason=FinishReason.STOP),
        ],
    )
    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "read k"},
    )
    assert result["tool_outcomes"][0]["decision"] == "allow"
