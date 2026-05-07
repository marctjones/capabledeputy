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
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import egress_label_for
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import CONFLICT_RULES, Decision
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
        return (
            self.parse_error is None
            and self.runtime_error is None
            and not self.violations
        )


def _starting_label_set(initial_scope: dict[str, LabeledValue] | None) -> frozenset[Label]:
    if not initial_scope:
        return frozenset()
    out: frozenset[Label] = frozenset()
    for value in initial_scope.values():
        out = out | value.labels
    return out


def _hypothetical_decide(
    label_set: frozenset[Label],
    kind: CapabilityKind,
) -> tuple[Decision, str | None, str | None]:
    """Predict the policy outcome for a tool call given an effective label
    set, ignoring capability matching. Capability matching depends on
    runtime grants which dry-run doesn't have; the conflict rules are the
    interesting part for static analysis since they encode the
    information-flow constraints.
    """
    egress = egress_label_for(kind)
    effective = label_set | ({egress} if egress else frozenset())
    for rule in CONFLICT_RULES:
        if rule.fires(effective):
            return (
                rule.decision,
                rule.name,
                f"rule {rule.name} fires on {sorted(label.value for label in effective)}",
            )
    return Decision.ALLOW, None, None


def _make_dry_run_caller(
    registry: ToolRegistry,
    starting_labels: frozenset[Label],
):
    """Build a ToolCaller that simulates a tool dispatch in dry-run mode.

    Each invocation:
      - Looks up the tool definition (errors if unknown).
      - Builds the predicted effective label set (starting + accumulated +
        the call's arg labels + tool's inherent labels).
      - Runs the conflict-rule check.
      - Returns a synthetic ToolDispatchResult with the inherent labels
        and a placeholder output.
    """
    accumulated_labels = {"value": starting_labels}

    async def caller(
        tool_name: str,
        args: dict[str, Any],
        arg_labels: frozenset[Label],
    ) -> ToolDispatchResult:
        try:
            tool = registry.get(tool_name)
        except ToolNotFoundError as e:
            return ToolDispatchResult(
                decision=Decision.DENY,
                rule=None,
                reason=str(e),
                inherent_labels=frozenset(),
                output=None,
            )
        effective = (
            accumulated_labels["value"]
            | arg_labels
            | tool.inherent_labels
        )
        decision, rule, reason = _hypothetical_decide(effective, tool.capability_kind)
        if decision == Decision.ALLOW:
            accumulated_labels["value"] = effective
            return ToolDispatchResult(
                decision=Decision.ALLOW,
                output={"_dry_run": True, "tool": tool_name},
                inherent_labels=tool.inherent_labels,
            )
        return ToolDispatchResult(
            decision=decision,
            output=None,
            inherent_labels=tool.inherent_labels,
            rule=rule,
            reason=reason,
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
    mode. The arg-level labels carried by each LabeledValue are unioned
    into the session BEFORE the policy decision runs, so per-value
    provenance contributes to the gate (turn-level mode only sees
    accumulated session labels — programmatic mode is more precise).

    A `mode.selected` audit event marked `mode=programmatic` fires per
    successful call so traces distinguish interpreter-driven calls from
    agent-loop-driven calls.
    """

    async def caller(
        tool_name: str,
        args: dict[str, Any],
        arg_labels: frozenset[Label],
    ) -> ToolDispatchResult:
        try:
            tool = registry.get(tool_name)
        except ToolNotFoundError as e:
            return ToolDispatchResult(
                decision=Decision.DENY,
                rule=None,
                reason=str(e),
                inherent_labels=frozenset(),
                output=None,
            )

        if arg_labels:
            await graph.add_labels(session_id, arg_labels)

        outcome = await tool_client.call_tool(session_id, tool_name, args)

        if audit is not None and outcome.decision == Decision.ALLOW:
            await audit.write(
                Event(
                    event_type=EventType.MODE_SELECTED,
                    session_id=session_id,
                    payload={
                        "mode": "programmatic",
                        "tool": tool_name,
                        "arg_labels": sorted(label.value for label in arg_labels),
                    },
                ),
            )

        return ToolDispatchResult(
            decision=outcome.decision,
            output=outcome.output,
            inherent_labels=tool.inherent_labels | outcome.labels_added,
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

    starting_labels = _starting_label_set(initial_scope)
    caller = _make_dry_run_caller(registry, starting_labels)
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
