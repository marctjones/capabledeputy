"""Bundle-collecting dry-run + approved-bundle execution.

Two entry points:

  - `dry_run_for_bundle(source, registry, ...)` — run the program
    symbolically; ALLOW calls return synthetic results; REQUIRE_APPROVAL
    calls also return synthetic results (defer the gate, don't halt);
    DENY calls record a non-negotiable WOULD_DENY entry. Returns a
    `WorkflowImpact` capturing every step.

  - `execute_with_approved_bundle(source, bundle, ...)` — re-run the
    program for real; each tool call's predicted gate is matched
    against the bundle by (step_index, tool_name); approved gates flip
    to ALLOW for this run; mismatch (program changed) aborts with a
    clear error.

The bundle's program_hash is verified before execution to catch the
case where the source changed between preview and run.
"""

from __future__ import annotations

from dataclasses import replace as _replace
from typing import Any
from uuid import UUID

from capabledeputy.approval.bundle import (
    BundledApproval,
    GateState,
    WorkflowImpact,
    WorkflowStep,
    hash_program,
)
from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.engine import _conflict_invariant_outcome
from capabledeputy.policy.labels import (
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
    most_restrictive_inherit,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.programmatic.errors import ProgramSyntaxError
from capabledeputy.programmatic.evaluator import (
    ToolDispatchResult,
    run_program,
)
from capabledeputy.programmatic.parser import parse_program
from capabledeputy.programmatic.value import LabeledValue
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolNotFoundError, ToolRegistry


class BundleMismatchError(RuntimeError):
    """Raised when an approved bundle doesn't match the program at
    execution time. Source was edited between preview and run, OR the
    bundle is from a different program."""


def _starting_label_state(
    initial_scope: dict[str, LabeledValue] | None,
) -> LabelState:
    if not initial_scope:
        return LabelState()
    states: list[LabelState] = []
    for value in initial_scope.values():
        states.append(value.label_state)
    return most_restrictive_inherit(*states) if states else LabelState()


async def dry_run_for_bundle(
    source: str,
    registry: ToolRegistry,
    *,
    initial_scope: dict[str, LabeledValue] | None = None,
) -> WorkflowImpact:
    """Symbolic execution that defers REQUIRE_APPROVAL gates.

    Differs from `dry_run_program` in one critical way: REQUIRE_APPROVAL
    decisions don't halt the program. The runner records the gate, then
    returns a synthetic LabeledValue (the inherent labels of the tool)
    so downstream steps can be analyzed too. The result is a complete
    impact tree for the entire workflow, with every approval gate
    visible up front.

    DENY decisions are also recorded but as non-negotiable
    `WOULD_DENY` gates: the user can't approve them away because they
    represent rules the user (or the system) explicitly forbade. A
    bundle containing a `WOULD_DENY` is not approvable.
    """
    impact = WorkflowImpact()

    try:
        module = parse_program(source)
    except ProgramSyntaxError as e:
        impact.parse_error = str(e)
        return impact

    impact.program_hash = hash_program(source)

    # Per-call accumulator the synthetic ToolCaller updates as it goes.
    state = {
        "step_counter": 0,
        "accumulated_label_state": _starting_label_state(initial_scope),
    }

    async def caller(
        tool_name: str,
        args: dict[str, Any],
        arg_label_state: LabelState,
    ) -> ToolDispatchResult:
        state["step_counter"] += 1
        step_index = state["step_counter"]

        try:
            tool = registry.get(tool_name)
        except ToolNotFoundError as e:
            # Render arg_label_state to flat format for backward-compat audit
            arg_categories = sorted(t.category for t in arg_label_state.a)
            arg_levels = sorted(t.level.value for t in arg_label_state.b)
            impact.steps.append(
                WorkflowStep(
                    step_index=step_index,
                    tool_name=tool_name,
                    args=args,
                    arg_labels=frozenset(list(arg_categories) + list(arg_levels)),
                    decision=Decision.DENY.value,
                    inherent_labels=frozenset(),
                    rule="tool-not-found",
                    reason=str(e),
                    line=None,
                ),
            )
            impact.gates.append(
                BundledApproval(
                    step_index=step_index,
                    tool_name=tool_name,
                    args=args,
                    arg_labels=frozenset(list(arg_categories) + list(arg_levels)),
                    rule="tool-not-found",
                    reason=str(e),
                    state=GateState.WOULD_DENY,
                ),
            )
            # Halt the dry-run: we can't predict downstream effects of
            # an unknown tool. Returning DENY ends the program.
            return ToolDispatchResult(
                decision=Decision.DENY,
                rule="tool-not-found",
                reason=str(e),
                tags_added=LabelState(),
            )

        # Compose four-axis tags: accumulated + args + tool inherent + per-arg
        per_arg_tags = tool.extract_arg_inherent_tags(args)
        effective = most_restrictive_inherit(
            state["accumulated_label_state"],
            arg_label_state,
            tool.inherent_tags,
            per_arg_tags,
        )

        # Check four-axis information-flow conflict invariants (same gates real
        # engine enforces). Record any REQUIRE_APPROVAL gates as deferred steps.
        # Render four-axis for backward-compat audit trails.
        arg_categories = sorted(t.category for t in arg_label_state.a)
        arg_levels = sorted(t.level.value for t in arg_label_state.b)
        tool_categories = sorted(t.category for t in tool.inherent_tags.a)
        tool_levels = sorted(t.level.value for t in tool.inherent_tags.b)

        from capabledeputy.policy.actions import Action

        fake_action = Action(kind=tool.capability_kind, target="", amount=0)
        conflict_outcome = _conflict_invariant_outcome(effective, fake_action)

        gate_decision = Decision.ALLOW
        gate_rule = None
        gate_reason = None

        if conflict_outcome is not None:
            gate_decision, gate_rule, gate_reason = conflict_outcome

        impact.steps.append(
            WorkflowStep(
                step_index=step_index,
                tool_name=tool_name,
                args=args,
                arg_labels=frozenset(list(arg_categories) + list(arg_levels)),
                decision=gate_decision.value,
                inherent_labels=frozenset(list(tool_categories) + list(tool_levels)),
                rule=gate_rule,
                reason=gate_reason,
                line=None,
            ),
        )

        # Record gates for non-ALLOW outcomes
        if gate_decision != Decision.ALLOW:
            gate_state = (
                GateState.WOULD_DENY if gate_decision == Decision.DENY else GateState.PENDING
            )
            impact.gates.append(
                BundledApproval(
                    step_index=step_index,
                    tool_name=tool_name,
                    args=args,
                    arg_labels=frozenset(list(arg_categories) + list(arg_levels)),
                    rule=gate_rule,
                    reason=gate_reason,
                    state=gate_state,
                ),
            )
            # For DENY, halt; for REQUIRE_APPROVAL, continue (deferred gates).
            if gate_decision == Decision.DENY:
                return ToolDispatchResult(
                    decision=Decision.DENY,
                    rule=gate_rule,
                    reason=gate_reason,
                    tags_added=LabelState(),
                )

        # Deferred gates (bundle-only behavior): allow the call to proceed
        # so downstream analysis sees the full workflow impact.
        state["accumulated_label_state"] = effective
        return ToolDispatchResult(
            decision=Decision.ALLOW,
            output={"_dry_run": True, "tool": tool_name},
            tags_added=LabelState(),
        )

    result = await run_program(module, caller, initial_scope=initial_scope)
    if result.error is not None:
        impact.runtime_error = result.error

    return impact


async def execute_with_approved_bundle(
    source: str,
    impact: WorkflowImpact,
    *,
    session_id: UUID,
    tool_client: LabeledToolClient,
    graph: SessionGraph,
    registry: ToolRegistry,
    audit: AuditWriter | None = None,
    initial_scope: dict[str, LabeledValue] | None = None,
) -> Any:
    """Re-run the program for real; each tool call's gate is matched
    against the impact's gates. Approved gates are pre-applied so the
    LabeledToolClient executes them as if they were ALLOW decisions
    (the actual policy gate becomes a no-op for this call). Any
    mismatch raises `BundleMismatchError`.

    Records `approval.approved` audit events for each pre-applied gate
    so the cross-session declassification path's existing audit shape
    holds.
    """
    if hash_program(source) != impact.program_hash:
        raise BundleMismatchError(
            "program source hash does not match the bundle's program_hash; "
            "the source changed between preview and execution",
        )
    if impact.has_blocking_deny:
        raise BundleMismatchError(
            "bundle contains non-negotiable DENY gate(s); cannot execute",
        )

    module = parse_program(source)

    # Counter-keyed lookup of the bundle's gates so we can match them
    # to the live tool calls in order.
    gates_by_index = {g.step_index: g for g in impact.gates if g.state == GateState.APPROVED}
    state = {"step_counter": 0}

    async def caller(
        tool_name: str,
        args: dict[str, Any],
        arg_label_state: LabelState,
    ) -> ToolDispatchResult:
        state["step_counter"] += 1
        step_index = state["step_counter"]

        try:
            tool = registry.get(tool_name)
        except ToolNotFoundError as e:
            return ToolDispatchResult(
                decision=Decision.DENY,
                rule="tool-not-found",
                reason=str(e),
                tags_added=LabelState(),
            )

        gate = gates_by_index.get(step_index)
        if gate is not None:
            if gate.tool_name != tool_name:
                raise BundleMismatchError(
                    f"step {step_index}: bundle says {gate.tool_name}, "
                    f"program calls {tool_name}; source changed mid-flight",
                )

            # Pre-approved: dispatch via a purpose-limited session
            # following the same cross-session declassification pattern
            # as the approval-queue's manual approval path. The gate's
            # args are taken from the BUNDLE — not the live call — so
            # what the user reviewed is byte-for-byte what executes.
            outcome = await _dispatch_via_purpose_session(
                graph=graph,
                tool_client=tool_client,
                tool_name=tool_name,
                args=gate.args,
                tool_kind=tool.capability_kind,
                target=str(gate.args.get(tool.target_arg, "")),
                origin_session=session_id,
                bundle_id=str(impact.bundle_id),
                step_index=step_index,
                audit=audit,
                rule=gate.rule,
            )
            return ToolDispatchResult(
                decision=outcome.decision,
                output=outcome.output,
                tags_added=LabelState(),
                rule=outcome.rule,
                reason=outcome.reason,
            )

        # No gate match → ordinary path.
        if arg_label_state.a or arg_label_state.b:
            await graph.add_tags(session_id, arg_label_state)
        outcome = await tool_client.call_tool(session_id, tool_name, args)
        return ToolDispatchResult(
            decision=outcome.decision,
            output=outcome.output,
            tags_added=LabelState(),
            rule=outcome.rule,
            reason=outcome.reason,
        )

    return await run_program(module, caller, initial_scope=initial_scope)


async def _dispatch_via_purpose_session(
    *,
    graph: SessionGraph,
    tool_client: LabeledToolClient,
    tool_name: str,
    args: dict[str, Any],
    tool_kind: CapabilityKind | str,
    target: str,
    origin_session: UUID,
    bundle_id: str,
    step_index: int,
    audit: AuditWriter | None,
    rule: str | None,
) -> Any:
    """Spawn a purpose-limited session, grant a one-shot capability,
    dispatch, abort. Mirrors `_execute_declassified_email` in
    daemon/approval_handlers.py — same security shape, applied per
    bundle gate.
    """
    cap = Capability(
        kind=tool_kind,
        pattern=target if target else "*",
        expiry=CapabilityExpiry.ONE_SHOT,
        origin=CapabilityOrigin.USER_APPROVED,
    )
    purpose = await graph.new(
        intent=f"bundle {bundle_id} step {step_index} ({tool_name})",
    )
    granted = await graph.grant_capability(purpose.id, cap)
    # Seed with principal-direct provenance (user-approved execution)
    principal_direct_tags = LabelState(
        b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)})
    )
    granted = _replace(
        granted,
        label_state=principal_direct_tags,
    )
    graph._sessions[granted.id] = granted

    if audit is not None:
        await audit.write(
            Event(
                event_type=EventType.APPROVAL_APPROVED,
                session_id=origin_session,
                payload={
                    "approval_id": f"bundle:{bundle_id}:{step_index}",
                    "decided_by": "user(bundle)",
                    "decision_scope": {
                        "bundle_id": bundle_id,
                        "step_index": step_index,
                        "rule": rule,
                        "executed_in_session": str(granted.id),
                    },
                },
            ),
        )

    outcome = await tool_client.call_tool(granted.id, tool_name, args)
    await graph.abort(granted.id)
    return outcome
