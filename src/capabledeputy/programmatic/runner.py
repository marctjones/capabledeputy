"""High-level entry points: run + dry-run a programmatic-mode source.

`run_program_against_session` dispatches every `call(tool, **kwargs)`
through `LabeledToolClient` so policy + audit + label propagation
behave identically to turn-level mode. A non-ALLOW decision raises
`ProgramPolicyError` and halts the program.

`dry_run_program` does symbolic execution: each tool call is checked
against the conflict rules using the union of the call args' predicted
labels and the tool's `inherent_labels`, and a synthetic LabeledValue
with the inherent labels is returned. No tool handlers run; no audit
events fire; no session state mutates. The return value is a report of
all predicted tool calls and any policy violations the program would
trigger if executed against the supplied (or empty) starting state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.engine import _conflict_invariant_outcome
from capabledeputy.policy.labels import AxisA, AxisB, LabelState, most_restrictive_inherit
from capabledeputy.policy.rules import Decision
from capabledeputy.programmatic.errors import ProgramSyntaxError
from capabledeputy.programmatic.evaluator import (
    ExecutionResult,
    ToolCallRecord,
    ToolDispatchResult,
    run_program,
)
from capabledeputy.programmatic.parser import parse_program
from capabledeputy.programmatic.value import LabeledValue
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolNotFoundError, ToolRegistry


@dataclass
class DryRunReport:
    """Report from dry_run_program. `violations` lists every call whose
    predicted decision is not ALLOW; `tool_calls` is the full ordered
    record so callers can render a step-by-step trace.
    """

    tool_calls: list[ToolCallRecord]
    violations: list[ToolCallRecord]
    parse_error: str | None = None
    runtime_error: str | None = None
    final_scope: dict[str, LabeledValue] = field(default_factory=dict)
    return_value: LabeledValue | None = None

    @property
    def ok(self) -> bool:
        return self.parse_error is None and self.runtime_error is None and not self.violations


def _starting_label_state(initial_scope: dict[str, LabeledValue] | None) -> LabelState:
    if not initial_scope:
        return LabelState()
    states: list[LabelState] = []
    for value in initial_scope.values():
        states.append(value.label_state)
    return most_restrictive_inherit(*states) if states else LabelState()


def _make_dry_run_caller(
    registry: ToolRegistry,
    starting_label_state: LabelState,
):
    """Build a ToolCaller that simulates a tool dispatch in dry-run mode.

    Each invocation:
      - Looks up the tool definition (errors if unknown).
      - Builds the predicted effective four-axis label state (starting +
        accumulated + the call's arg labels + tool's inherent tags).
      - Dry-run can only be optimistic: it doesn't gate on capability
        availability, expiry, rate-limits, or approval requirements
        (those depend on live session state). Enforcement is done by the
        real `LabeledToolClient.call_tool()` at dispatch time.
      - Returns a synthetic ToolDispatchResult with the inherent tags
        and a placeholder output.
    """
    accumulated_state = {"value": starting_label_state}

    async def caller(
        tool_name: str,
        args: dict[str, Any],
        arg_label_state: LabelState,
    ) -> ToolDispatchResult:
        try:
            tool = registry.get(tool_name)
        except ToolNotFoundError as e:
            return ToolDispatchResult(
                decision=Decision.DENY,
                rule=None,
                reason=str(e),
                tags_added=LabelState(),
                output=None,
            )
        # Compose four-axis tags: accumulated + args + tool inherent + per-arg inherent
        per_arg_tags = tool.extract_arg_inherent_tags(args)
        effective = most_restrictive_inherit(
            accumulated_state["value"],
            arg_label_state,
            tool.inherent_tags,
            per_arg_tags,
        )

        # Check four-axis information-flow conflict invariants (the same gates
        # the real engine enforces). Dry-run is optimistic on everything else
        # (no capability checks, no rate limits, no approvals).
        from capabledeputy.policy.actions import Action

        fake_action = Action(kind=tool.capability_kind, target="", amount=0)
        axis_a = AxisA(categories=tuple(effective.a))
        axis_b = AxisB(entries=tuple(effective.b))
        conflict_outcome = _conflict_invariant_outcome(axis_a, axis_b, fake_action)
        if conflict_outcome is not None:
            decision, rule, reason = conflict_outcome
            return ToolDispatchResult(
                decision=decision,
                rule=rule,
                reason=reason,
                tags_added=LabelState(),
            )

        # Dry-run allows when no information-flow conflicts fire.
        accumulated_state["value"] = effective
        return ToolDispatchResult(
            decision=Decision.ALLOW,
            output={"_dry_run": True, "tool": tool_name},
            tags_added=LabelState(),
        )

    return caller


def _make_real_caller(
    tool_client: LabeledToolClient,
    session_id: UUID,
    graph: SessionGraph,
    registry: ToolRegistry,
    audit: AuditWriter | None = None,
):
    """Build a ToolCaller that dispatches through LabeledToolClient.

    Policy + label propagation + audit happen identically to turn-level
    mode. The arg-level tags carried by each LabeledValue are composed
    into the session BEFORE the policy decision runs, so per-value
    provenance contributes to the gate (turn-level mode only sees
    accumulated session tags — programmatic mode is more precise).

    A `mode.selected` audit event marked `mode=programmatic` fires per
    successful call so traces distinguish interpreter-driven calls from
    agent-loop-driven calls.
    """

    async def caller(
        tool_name: str,
        args: dict[str, Any],
        arg_label_state: LabelState,
    ) -> ToolDispatchResult:
        try:
            _ = registry.get(tool_name)
        except ToolNotFoundError as e:
            return ToolDispatchResult(
                decision=Decision.DENY,
                rule=None,
                reason=str(e),
                tags_added=LabelState(),
                output=None,
            )

        if arg_label_state.a or arg_label_state.b:
            await graph.add_tags(session_id, arg_label_state)

        outcome = await tool_client.call_tool(session_id, tool_name, args)

        if audit is not None and outcome.decision == Decision.ALLOW:
            # Emit audit with four-axis delta
            delta_a = sorted(t.category for t in arg_label_state.a)
            delta_b = sorted(t.level.value for t in arg_label_state.b)
            await audit.write(
                Event(
                    event_type=EventType.MODE_SELECTED,
                    session_id=session_id,
                    payload={
                        "mode": "programmatic",
                        "tool": tool_name,
                        "arg_categories": delta_a,
                        "arg_provenance": delta_b,
                    },
                ),
            )

        return ToolDispatchResult(
            decision=outcome.decision,
            output=outcome.output,
            tags_added=LabelState(),
            rule=outcome.rule,
            reason=outcome.reason,
        )

    return caller


async def dry_run_program(
    source: str,
    registry: ToolRegistry,
    *,
    initial_scope: dict[str, LabeledValue] | None = None,
) -> DryRunReport:
    try:
        module = parse_program(source)
    except ProgramSyntaxError as e:
        return DryRunReport(
            tool_calls=[],
            violations=[],
            parse_error=str(e),
        )

    starting_state = _starting_label_state(initial_scope)
    caller = _make_dry_run_caller(registry, starting_state)
    result = await run_program(module, caller, initial_scope=initial_scope)

    violations = [c for c in result.tool_calls if c.decision != Decision.ALLOW]
    return DryRunReport(
        tool_calls=result.tool_calls,
        violations=violations,
        runtime_error=result.error,
        final_scope=result.final_scope,
        return_value=result.return_value,
    )


async def run_program_against_session(
    source: str,
    *,
    session_id: UUID,
    tool_client: LabeledToolClient,
    graph: SessionGraph,
    registry: ToolRegistry,
    audit: AuditWriter | None = None,
    initial_scope: dict[str, LabeledValue] | None = None,
) -> ExecutionResult:
    module = parse_program(source)
    caller = _make_real_caller(tool_client, session_id, graph, registry, audit)
    return await run_program(module, caller, initial_scope=initial_scope)


__all__ = [
    "DryRunReport",
    "dry_run_program",
    "run_program_against_session",
]
