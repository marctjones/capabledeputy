"""Demo 12 — programmatic mode end-to-end.

The planner LLM emits a single Python program describing the entire
data flow. The harness parses it against the AST subset, dry-runs to
preview policy decisions, and executes. The user sees the whole plan
before any tool fires.

This composes the v0.3 programmatic mode (interpreter + analyzer +
planner loop) with v0.5's bundled approvals — a workflow with several
gates produces ONE bundle the user reviews and approves.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.programmatic import dry_run_for_bundle, dry_run_program
from capabledeputy.programmatic.value import LabeledValue


async def test_planner_emits_program_and_runs(tmp_path: Path) -> None:
    """End-to-end: session prefer_programmatic=True; agent loop
    routes to the programmatic planner; planner emits a code block;
    harness parses + executes."""
    program = """\
I'll read the user's note then save a copy with a clear name.

```python
note = call("memory.read", key="source")
saved = call("memory.write", key="copy", value=note["value"])
```
"""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient(
            [LLMResponse(content=program, finish_reason=FinishReason.STOP)],
        ),
    )
    await app.startup()
    s = await app.graph.new(prefer_programmatic=True)
    caps = frozenset(
        {
            Capability(kind=CapabilityKind.READ_FS, pattern="*"),
            Capability(kind=CapabilityKind.WRITE_FS, pattern="*"),
        },
    )
    app.graph._sessions[s.id] = replace(s, capability_set=caps)
    app.memory.write("source", "hello world", frozenset())

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "Save a copy of my note."},
    )
    assert len(result["tool_outcomes"]) == 2
    assert all(o["decision"] == "allow" for o in result["tool_outcomes"])
    entry = app.memory.read("copy")
    assert entry is not None and entry.value == "hello world"

    events = await app.audit.read_all()
    modes = [
        e.payload.get("mode")
        for e in events
        if e.event_type.value == "mode.selected"
    ]
    assert ExecutionMode.PROGRAMMATIC.value in modes


async def test_dry_run_catches_violation_before_execution(tmp_path: Path) -> None:
    """The static analyzer flags the violation BEFORE any tool fires.
    The user sees the program and the predicted denial without side
    effects."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()

    # The program reads a labeled-health note then tries to email it.
    src = """
labs = call("memory.read", key="rx")
sent = call("email.send", to="random@example.com", subject="x", body="y")
"""
    # Pre-populate so memory.read returns the labeled value.
    app.memory.write("rx", "lisinopril 10mg", frozenset({Label.CONFIDENTIAL_HEALTH}))

    initial_scope = {
        "_taint": LabeledValue(
            raw=None,
            labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
        ),
    }
    report = await dry_run_program(src, app.registry, initial_scope=initial_scope)
    assert not report.ok
    assert any(v.tool_name == "email.send" for v in report.violations)
    assert any(v.rule == "health-meets-egress" for v in report.violations)

    # Critical: NOTHING was dispatched.
    assert app.email_outbox.all() == []


async def test_bundle_collects_multiple_gates_into_one_review(tmp_path: Path) -> None:
    """Composes programmatic mode with bundled approvals: a workflow
    with three approval-required actions produces a single bundle the
    user reviews end-to-end."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    src = """
a = call("purchase.queue", vendor="vendor-a", item="x", amount=10)
b = call("purchase.queue", vendor="vendor-b", item="y", amount=20)
c = call("purchase.queue", vendor="vendor-c", item="z", amount=30)
"""
    initial_scope = {
        "_taint": LabeledValue(
            raw=None,
            labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        ),
    }
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial_scope)
    assert len(impact.steps) == 3
    assert len(impact.gates) == 3
    assert impact.is_approvable  # all REQUIRE_APPROVAL, no DENY
    # Three distinct gates → one decision (impact.approve_all).
