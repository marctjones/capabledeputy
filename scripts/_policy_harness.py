"""Shared deterministic policy-engine harness.

No real LLM, no network. A scenario primes a FakeLLMClient with a
hardcoded tool-call cassette, runs it through the *real* agent loop and
the *real* deterministic policy engine, then asserts the engine's
decision (and optionally the rule) for each tool call against a
hardcoded expectation.

The themed `scripts/policy_*.py` files import this module, declare a
list of `Scenario`s, and call `run_suite(...)`. Same inputs -> same
decisions -> same exit code (0 = all met, 1 = any mismatch).
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability
from capabledeputy.policy.labels import tags_for_labels_strings


def tc(call_id: str, name: str, **args: object) -> ToolCall:
    """One scripted tool call."""
    return ToolCall(id=call_id, name=name, args=dict(args))


def tool_turn(content: str, *calls: ToolCall) -> LLMResponse:
    """An assistant turn that invokes one or more tools."""
    return LLMResponse(
        content=content,
        tool_calls=calls,
        finish_reason=FinishReason.TOOL_CALLS,
    )


def final(content: str = "done") -> LLMResponse:
    """The terminal assistant turn (no tool calls)."""
    return LLMResponse(content=content, finish_reason=FinishReason.STOP)


@dataclass(frozen=True)
class Expect:
    """One asserted policy outcome for one tool call, in order."""

    tool: str
    decision: str  # "allow" | "deny" | "require_approval"
    rule_contains: str | None = None  # substring check; None = don't care


@dataclass
class Scenario:
    name: str
    why: str
    caps: frozenset[Capability]
    responses: list[LLMResponse]
    expect: list[Expect]
    session_labels: frozenset[str] = frozenset()
    # Optional seeding hook: receives the started App; use it to write
    # labeled memory, add inbox messages, create calendar events, etc.
    pre: Callable[[App], None] | None = field(default=None)
    # Optional quarantined-LLM cassette. Required only by scenarios that
    # exercise quarantined.extract (the dual-LLM declassifier path); the
    # planner LLM never sees this, and enforcement is unaffected.
    quarantined: list[LLMResponse] | None = field(default=None)


async def run_scenario(sc: Scenario) -> list[str]:
    """Run one scenario; return failure strings (empty list = pass)."""
    from dataclasses import replace

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        app = App(
            state_db_path=tmp / "state.db",
            audit_log_path=tmp / "audit.jsonl",
            llm_client=FakeLLMClient(sc.responses),
            quarantined_llm=(FakeLLMClient(sc.quarantined) if sc.quarantined is not None else None),
        )
        await app.startup()

        if sc.pre is not None:
            sc.pre(app)

        s = await app.graph.new(intent=f"harness:{sc.name}")
        seed = tags_for_labels_strings(sc.session_labels)
        app.graph._sessions[s.id] = replace(
            s,
            capability_set=sc.caps,
            axis_a=seed.to_axis_a(),
            axis_b=seed.to_axis_b(),
        )

        handlers = make_agent_handlers(app)
        result = await handlers["session.send"](
            {"session_id": str(s.id), "message": f"run {sc.name}"},
        )

        outcomes = result.get("tool_outcomes", [])
        if len(outcomes) != len(sc.expect):
            failures.append(
                f"expected {len(sc.expect)} tool outcome(s), got "
                f"{len(outcomes)}: {[o.get('tool_name') for o in outcomes]}",
            )

        for i, exp in enumerate(sc.expect):
            if i >= len(outcomes):
                failures.append(f"#{i} missing: expected {exp.tool} {exp.decision}")
                continue
            o = outcomes[i]
            got_tool = o.get("tool_name")
            got_decision = o.get("decision")
            got_rule = o.get("rule")
            if got_tool != exp.tool:
                failures.append(f"#{i} tool: expected {exp.tool}, got {got_tool}")
            if got_decision != exp.decision:
                failures.append(
                    f"#{i} {exp.tool} decision: expected {exp.decision}, "
                    f"got {got_decision} (rule={got_rule})",
                )
            if exp.rule_contains and exp.rule_contains not in (got_rule or ""):
                failures.append(
                    f"#{i} {exp.tool} rule: expected to contain "
                    f"'{exp.rule_contains}', got '{got_rule}'",
                )
    return failures


async def run_suite(title: str, scenarios: list[Scenario]) -> int:
    """Run every scenario, print a report, return a process exit code."""
    bar = "=" * 68
    print(bar)
    print(f"Policy harness — {title}  ({len(scenarios)} scenarios, no real LLM)")
    print(bar)
    total_fail = 0
    for sc in scenarios:
        failures = await run_scenario(sc)
        status = "PASS" if not failures else "FAIL"
        print(f"\n[{status}] {sc.name}")
        print(f"       {sc.why}")
        for exp in sc.expect:
            tail = f"  (rule~='{exp.rule_contains}')" if exp.rule_contains else ""
            print(f"       expect: {exp.tool:<22} -> {exp.decision}{tail}")
        for f in failures:
            print(f"       MISMATCH: {f}")
        total_fail += len(failures)

    print("\n" + bar)
    if total_fail:
        print(f"RESULT: FAIL — {title}: {total_fail} mismatch(es)")
        return 1
    print(f"RESULT: PASS — {title}: all {len(scenarios)} scenarios as scripted")
    return 0
