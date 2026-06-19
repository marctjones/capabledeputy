"""Programmatic mode planner loop.

The LLM emits a Python program in a fenced code block; the harness
parses + runs it through the AST-subset interpreter. Tool calls go
through LabeledToolClient identically to turn-level mode.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from capabledeputy.agent.loop import run_turn
from capabledeputy.agent.programmatic_loop import (
    extract_code_block,
    run_programmatic_turn,
)
from capabledeputy.app import App
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier


def test_extract_code_block_finds_python_block() -> None:
    text = """\
Here is my plan:

```python
x = call("memory.read", key="k")
```

That's it.
"""
    code = extract_code_block(text)
    assert code is not None
    assert 'call("memory.read"' in code


def test_extract_code_block_finds_unfenced_language() -> None:
    text = "```\nx = 1\n```"
    assert extract_code_block(text) == "x = 1\n"


def test_extract_code_block_returns_none_when_absent() -> None:
    assert extract_code_block("just prose, no block") is None


def test_extract_code_block_picks_first_block() -> None:
    text = "```python\na = 1\n```\nthen\n```python\nb = 2\n```"
    code = extract_code_block(text)
    assert code is not None
    assert "a = 1" in code
    assert "b = 2" not in code


async def test_programmatic_loop_executes_emitted_program(tmp_path: Path) -> None:
    """End-to-end: FakeLLM emits a program; harness runs it; tool fires."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()
    s = await app.graph.new(intent="programmatic loop test")
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    app.memory.write("k", "v", LabelState())

    program = """\
I'll read the value at key k.

```python
note = call("memory.read", key="k")
return note
```
"""
    app.llm_client = FakeLLMClient(
        [LLMResponse(content=program, finish_reason=FinishReason.STOP)],
    )

    result = await run_programmatic_turn(
        session_id=s.id,
        user_message="read k",
        llm=app.llm_client,
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
    )
    assert len(result.tool_outcomes) == 1
    assert result.tool_outcomes[0].decision.value == "allow"
    # The harness appends the program-return summary to the agent message.
    assert "program returned" in result.content


async def test_programmatic_loop_redacts_labeled_return_value(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()
    s = await app.graph.new(intent="programmatic labeled return test")
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    app.memory.write(
        "labs",
        "BP=120/80",
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            ),
        ),
    )

    program = """\
```python
note = call("memory.read", key="labs")
return note
```
"""
    app.llm_client = FakeLLMClient(
        [LLMResponse(content=program, finish_reason=FinishReason.STOP)],
    )

    result = await run_programmatic_turn(
        session_id=s.id,
        user_message="read labs",
        llm=app.llm_client,
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
    )

    assert len(result.tool_outcomes) == 1
    assert result.tool_outcomes[0].decision.value == "allow"
    assert "BP=120/80" not in result.content
    assert "raw value withheld" in result.content
    assert "health:regulated" in result.content


async def test_programmatic_loop_no_code_block_falls_through(tmp_path: Path) -> None:
    """A response with no code block is treated as a final answer."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient(
            [
                LLMResponse(
                    content="I cannot help with that request.",
                    finish_reason=FinishReason.STOP,
                ),
            ],
        ),
    )
    await app.startup()
    s = await app.graph.new()

    result = await run_programmatic_turn(
        session_id=s.id,
        user_message="x",
        llm=app.llm_client,  # type: ignore[arg-type]
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
    )
    assert len(result.tool_outcomes) == 0
    assert "cannot help" in result.content


async def test_programmatic_prompt_lists_only_visible_tools(tmp_path: Path) -> None:
    llm = FakeLLMClient(
        [
            LLMResponse(
                content="I will not run a program.",
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=llm,
    )
    await app.startup()
    s = await app.graph.new()
    read_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({read_cap}))

    await run_programmatic_turn(
        session_id=s.id,
        user_message="what tools are available?",
        llm=llm,
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
    )

    [(messages, _tools)] = llm.calls
    system_prompt = messages[0].content
    assert "memory.read" in system_prompt
    assert "purchase.queue" not in system_prompt
    assert "email.send" not in system_prompt


async def test_programmatic_loop_rejects_forbidden_program(tmp_path: Path) -> None:
    """Program with `import` is rejected at parse time without running."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient(
            [
                LLMResponse(
                    content="```python\nimport os\nx = os.system('ls')\n```",
                    finish_reason=FinishReason.STOP,
                ),
            ],
        ),
    )
    await app.startup()
    s = await app.graph.new()

    result = await run_programmatic_turn(
        session_id=s.id,
        user_message="x",
        llm=app.llm_client,  # type: ignore[arg-type]
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
    )
    assert len(result.tool_outcomes) == 0
    assert "rejected" in result.content
    assert "Import" in result.content or "import" in result.content


async def test_programmatic_loop_halts_on_policy_deny(tmp_path: Path) -> None:
    """A program that triggers a policy DENY is halted with the rule recorded."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()
    s = await app.graph.new(intent="programmatic deny")
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
    app.memory.write(
        "labs",
        "lisinopril",
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )

    program = """\
```python
labs = call("memory.read", key="labs")
purchase = call("purchase.queue", vendor="pharmacy", item=labs, amount=50)
```
"""
    app.llm_client = FakeLLMClient(
        [LLMResponse(content=program, finish_reason=FinishReason.STOP)],
    )

    result = await run_programmatic_turn(
        session_id=s.id,
        user_message="run plan",
        llm=app.llm_client,
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
    )
    decisions = [o.decision.value for o in result.tool_outcomes]
    assert "deny" in decisions
    assert "halted" in result.content


async def test_run_turn_dispatches_to_programmatic_when_session_prefers(
    tmp_path: Path,
) -> None:
    """run_turn (the top-level entry) routes to the programmatic loop
    automatically when session.prefer_programmatic is set.
    """
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()
    s = await app.graph.new(prefer_programmatic=True)
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    app.memory.write("k", "v", LabelState())

    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content='```python\nx = call("memory.read", key="k")\nreturn x\n```',
                finish_reason=FinishReason.STOP,
            ),
        ],
    )

    result = await run_turn(
        session_id=s.id,
        user_message="read",
        llm=app.llm_client,  # type: ignore[arg-type]
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
    )
    assert len(result.tool_outcomes) == 1
    events = await app.audit.read_all()
    mode_events = [e for e in events if e.event_type.value == "mode.selected"]
    assert any(e.payload.get("mode") == "programmatic" for e in mode_events)


async def test_run_turn_force_mode_programmatic(tmp_path: Path) -> None:
    """`force_mode` argument forces programmatic for one turn even
    when the session flag is off.
    """
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    app.memory.write("k", "v", LabelState())

    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content='```python\nx = call("memory.read", key="k")\n```',
                finish_reason=FinishReason.STOP,
            ),
        ],
    )

    result = await run_turn(
        session_id=s.id,
        user_message="read",
        llm=app.llm_client,  # type: ignore[arg-type]
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
        force_mode=ExecutionMode.PROGRAMMATIC,
    )
    assert len(result.tool_outcomes) == 1
