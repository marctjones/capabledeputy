"""Policy-gated, label-propagating tool dispatcher.

The LabeledToolClient is the single chokepoint between the agent loop
and any tool. Every dispatch goes through it, which guarantees that:

  - Capability + Brewer-Nash policy is checked before the handler runs.
  - The session's label set is updated with the tool's inherent labels
    plus any additional labels the handler returned.
  - Every step (policy decision, dispatch, return, label propagation)
    is recorded as a typed audit event (DESIGN.md §9.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.patterns.reference_handle import (
    ReferenceHandleError,
    ReferenceHandleStore,
    is_planner_safe_token,
)
from capabledeputy.policy.actions import Action
from capabledeputy.policy.assurance import (
    ResidualRiskThresholds,
    should_emit_residual_risk,
)
from capabledeputy.policy.bindings import BindingSet
from capabledeputy.policy.decision_rules import DecisionRules
from capabledeputy.policy.engine import PolicyDecision, decide
from capabledeputy.policy.envelope import EnvelopeSet, RiskPreference
from capabledeputy.policy.labels import Label
from capabledeputy.policy.overrides import OverrideGrantStore, OverridePolicies
from capabledeputy.policy.reversibility import ReversibilityLabel
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolRegistry


def _replace_tool_result(
    original: Any,
    *,
    output: Any = None,
    additional_labels: frozenset[Label] | None = None,
) -> Any:
    """Build a new ToolResult preserving fields that weren't overridden.

    Spec 004 P0 declassifier wire-in uses this to swap the tool's
    output for the declassifier's transformed value while keeping
    everything else intact.
    """
    from dataclasses import replace as _dc_replace

    kwargs: dict[str, Any] = {}
    if output is not None:
        kwargs["output"] = output
    if additional_labels is not None:
        kwargs["additional_labels"] = additional_labels
    return _dc_replace(original, **kwargs)


@dataclass(frozen=True)
class PolicyContext:
    """Operator-curated context the dispatcher needs to invoke the v2
    decision pipeline. Each field is independently optional —
    LabeledToolClient back-compat lives by passing `None`. Production
    code constructs this once at daemon start and injects it into the
    LabeledToolClient.
    """

    rules_v2: DecisionRules | None = None
    bindings: BindingSet | None = None
    override_policies: OverridePolicies | None = None
    override_grants: OverrideGrantStore | None = None
    handle_store: ReferenceHandleStore | None = None
    envelope_set: EnvelopeSet | None = None
    risk_preference: RiskPreference | None = None
    clearance_max_tier: Tier | None = None
    integrity_floor_level: str | None = None
    residual_risk_thresholds: ResidualRiskThresholds | None = None
    risk_register: Any = None
    # SandboxActuator (substrate port): None when no provider is
    # configured. The legacy `sandbox_actuator_wired` bool is now a
    # derived property below — kept for callers that only need the
    # presence check (e.g. the policy engine's fail-closed gate).
    sandbox_actuator: Any = None  # SandboxActuator | None — Any to avoid import cycle
    # FR-025 raise-only inspectors. Run on every tool return so any
    # taint the inspector identifies is added to the session's axes.
    # Composition is monotone (most_restrictive_inherit); inspectors
    # cannot CLEAR taint, only RAISE it.
    inspectors: tuple[Any, ...] = ()
    # DecisionInspectors run AFTER the standard policy decision and
    # may relax (loosen) or tighten (strengthen) the outcome. Composes
    # monotonically: tighten beats relax (most-restrictive wins).
    # Foundation for operator-authored decision refinement (Starlark
    # primitives, OPA consultation, etc.).
    decision_inspectors: tuple[Any, ...] = ()
    # DeclassifyingTransformers run on tool output AFTER inspectors,
    # BEFORE label propagation. Each transforms the value and emits a
    # structural-proof. The session sees the transformed value with
    # fewer labels attached (declassifier reduces per-result label
    # propagation; session label_set still grows monotonically).
    # Operator-declared order; sequential composition (each sees the
    # previous one's output).
    declassifiers: tuple[Any, ...] = ()
    # 003 runtime activation — Profiles registry keyed by profile_id.
    # When a session has clearance_profile_id set, the dispatcher
    # derives per-session clearance_max_tier (FR-008 BLP) +
    # integrity_floor_level (FR-004 Biba) from the matching profile.
    # Without this, BLP/Biba are library-only and never fire.
    profiles: dict[str, Any] = field(default_factory=dict)
    # Purposes registry — when a session has purpose_handle set and
    # the resolved Purpose has per-purpose bindings, the dispatcher
    # composes them with the global BindingSet for that one decide()
    # call. Most-specific-wins resolution means a purpose's narrow
    # paths override broad global rules without explicit precedence.
    # None ⇒ no per-purpose binding composition; global bindings
    # only.
    purposes: Any = None

    @property
    def sandbox_actuator_wired(self) -> bool:
        """Back-compat shim: True iff a SandboxActuator provider is
        plugged in. The policy engine's fail-closed gate on
        EXECUTE.sandbox uses just this bool; callers that need the
        actuator itself read `sandbox_actuator`."""
        return self.sandbox_actuator is not None


def build_policy_decided_payload(
    tool_name: str,
    args: dict[str, Any],
    decision: PolicyDecision,
) -> dict[str, Any]:
    """Construct the JSON payload for a `policy.decided` audit event.

    The base payload mirrors the v0.7 surface for back-compat.
    When the decision was reached with the v2 (003 US2) axis-based
    evaluator in play, two additional fields land in the payload:
    `v2_outcome` (the RuleOutcome the evaluator produced — AUTO/
    SUGGEST/REQUIRE_APPROVAL/DENY) and `v2_matched_rule_ids` (the
    sorted ids of human-ratified rules that matched). Together these
    give T041 audit-reconstruction enough to replay the v2 leg of
    the composition (FR-021).

    Omitted entirely when no v2 evaluation ran (back-compat reads of
    a pre-Phase-4 trace must not see new keys).
    """
    payload: dict[str, Any] = {
        "tool": tool_name,
        "args": args,
        "decision": decision.decision.value,
        "rule": decision.rule,
        "reason": decision.reason,
        "effective_labels": sorted(label.value for label in decision.effective_labels),
    }
    if decision.v2_outcome is not None:
        payload["v2_outcome"] = decision.v2_outcome.value
        payload["v2_matched_rule_ids"] = list(decision.v2_matched_rule_ids)
    if decision.refused_relax_inputs:
        payload["refused_relax_inputs"] = [
            {"description": r.description, "origin": r.origin}
            for r in decision.refused_relax_inputs
        ]
    # T048 — full Axis-A/B/D snapshot when v2 ran. Enough for T041
    # audit-reconstruction to rebuild AxisA/B/D from the payload and
    # replay evaluate() to the same outcome (FR-021).
    if decision.axis_a_snapshot is not None:
        payload["axis_a"] = decision.axis_a_snapshot.to_dict()
    if decision.axis_b_snapshot is not None:
        payload["axis_b"] = decision.axis_b_snapshot.to_dict()
    if decision.axis_d_snapshot is not None:
        payload["axis_d"] = decision.axis_d_snapshot.to_dict()
    if decision.effect_class is not None:
        payload["effect_class"] = decision.effect_class
    return payload


def build_relaxation_refused_payload(
    tool_name: str,
    args: dict[str, Any],
    decision: PolicyDecision,
) -> dict[str, Any]:
    """Construct the JSON payload for a `policy.relaxation_refused`
    audit event (T046 / FR-031).

    Emitted alongside the ordinary `policy.decided` event whenever a
    decision carries refused relax inputs — gives auditors a
    stand-alone, alertable event capturing the FR-031 violation and
    the offending origins.
    """
    return {
        "tool": tool_name,
        "args": args,
        "refused_relax_inputs": [
            {"description": r.description, "origin": r.origin}
            for r in decision.refused_relax_inputs
        ],
        "decision_rule": decision.rule,
        "decision_reason": decision.reason,
    }


@dataclass(frozen=True)
class ToolCallOutcome:
    decision: Decision
    output: dict[str, Any] | None = None
    rule: str | None = None
    reason: str | None = None
    labels_added: frozenset[Label] = field(default_factory=frozenset)
    error: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    # Populated only on REQUIRE_APPROVAL when the tool declares an
    # approval_route. A resolved dict (action, target, payload,
    # justification) describing the request. Kept on the outcome for
    # observability; clients no longer need to submit it themselves.
    approval_submission: dict[str, Any] | None = None
    # The id of the approval request the runtime registered in the
    # queue for this REQUIRE_APPROVAL outcome. Clients observe this
    # and route the user to `/approve <id>` — they do NOT register
    # the approval themselves. None if no queue is wired (unit tests)
    # or the tool declares no approval_route.
    approval_id: int | None = None
    # Issue #3 — Recovery-step sequence from `engine.decide()`. Empty
    # for ALLOW outcomes and for rules without slash-command recovery.
    # Renders in the REPL via `_render_recovery_steps`; the agent
    # quotes these literally instead of inventing commands.
    recovery_steps: tuple[Any, ...] = field(default_factory=tuple)


class LabeledToolClient:
    def __init__(
        self,
        registry: ToolRegistry,
        graph: SessionGraph,
        audit: AuditWriter,
        approval_queue: Any = None,
        policy_context: PolicyContext | None = None,
    ) -> None:
        self._registry = registry
        self._graph = graph
        self._audit = audit
        # Optional so unit tests can construct the client without the
        # full App. When present, REQUIRE_APPROVAL outcomes are
        # registered in the queue here, at the policy chokepoint —
        # not by whichever client happens to be driving the session.
        self._approval_queue = approval_queue
        # 003 composition sub-phase A — when provided, the v2 four-axis
        # decision pipeline activates. When None, the client behaves
        # exactly as v0.7 (back-compat).
        self._policy_context = policy_context

    @property
    def policy_context(self) -> PolicyContext | None:
        """Read-only accessor so callers (e.g. the agent loop building
        an LLM-context summary) don't have to reach into a private."""
        return self._policy_context

    async def call_tool(
        self,
        session_id: UUID,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolCallOutcome:
        tool = self._registry.get(tool_name)
        session = self._graph.get(session_id)

        action = Action(
            kind=tool.capability_kind,
            target=tool.extract_target(args),
            amount=tool.extract_amount(args),
        )
        # One authoritative decision clock per dispatch — resolved here
        # at the chokepoint, threaded into decide() AND reused as the
        # recorded use timestamp so the rate-limit window is consistent.
        dispatch_now = datetime.now(UTC)
        v2_kwargs = self._build_v2_decide_kwargs(session, tool)
        # 002 US2: pass the session's revoked_audit_ids so any
        # capability inert under cascade is denied at decide time.
        # Default-tolerant: pre-002 sessions deserialize with the
        # empty set, which is a no-op here.
        policy_decision = decide(
            session.label_set,
            session.capability_set,
            action,
            used_kinds=session.used_kinds,
            now=dispatch_now,
            cap_uses=session.cap_uses,
            revoked_audit_ids=getattr(session, "revoked_audit_ids", frozenset()),
            **v2_kwargs,
        )

        # DecisionInspector hook: registered inspectors run AFTER the
        # standard decision and may relax (loosen) or tighten
        # (strengthen) the outcome. Tighten beats Relax; non-monotone
        # moves are rejected at composition time.
        policy_decision = await self._apply_decision_inspectors(
            session_id,
            session,
            action,
            tool_name,
            policy_decision,
        )

        await self._emit_policy_decision(session_id, tool_name, args, policy_decision, tool)
        await self._emit_capability_checked(session_id, action, policy_decision)

        if policy_decision.decision != Decision.ALLOW:
            approval_submission: dict[str, Any] | None = None
            approval_id: int | None = None
            if (
                policy_decision.decision == Decision.REQUIRE_APPROVAL
                and tool.approval_route is not None
            ):
                approval_submission = tool.approval_route.resolve(
                    tool_name,
                    args,
                    policy_decision.reason or "",
                )
                # Register the approval here — at the policy chokepoint —
                # not in whichever client drives the session. This is
                # the only place that observes every REQUIRE_APPROVAL,
                # so capdep send / MCP / REPL all get queueing for free.
                if self._approval_queue is not None:
                    approval_id = await self._register_approval(
                        session_id,
                        session.label_set,
                        approval_submission,
                    )
            return ToolCallOutcome(
                decision=policy_decision.decision,
                rule=policy_decision.rule,
                reason=policy_decision.reason,
                tool_name=tool_name,
                tool_args=args,
                approval_submission=approval_submission,
                approval_id=approval_id,
                recovery_steps=policy_decision.recovery_steps,
            )

        await self._audit.write(
            Event(
                event_type=EventType.TOOL_DISPATCHED,
                session_id=session_id,
                payload={
                    "tool": tool_name,
                    "args": args,
                    "audit_id": str(
                        policy_decision.matched_capability.audit_id
                        if policy_decision.matched_capability
                        else "",
                    ),
                },
            ),
        )

        # Record the kind as "used" so future capabilities with
        # revoked_by={this_kind} are denied. Done before the handler
        # runs: a side-effecting handler that raises still counts as
        # a use for the purposes of revocation.
        await self._graph.record_used_kind(session_id, action.kind)

        # Record this dispatch against the matched capability's
        # sliding-window rate-limit log (keyed by audit_id). Same
        # timestamp as the decision clock. Pruned to the capability's
        # window so the log stays bounded.
        #
        # 002 US2-4 FR-015 pooled rate accounting: when the matched
        # capability is delegated, also record the use against every
        # ancestor up the parent_audit_id chain. A child cannot
        # out-spend its ancestor: each granted descendant dispatch
        # decrements the ancestor's window too.
        matched = policy_decision.matched_capability
        if matched is not None and matched.rate_limit is not None:
            from datetime import timedelta

            await self._graph.record_cap_use(
                session_id,
                str(matched.audit_id),
                dispatch_now,
                prune_older_than=timedelta(
                    seconds=matched.rate_limit.window_seconds,
                ),
            )
            # Pooled fan-out: walk parent_audit_id upward. The ancestor
            # may live in any session up the spawn chain (the session
            # that delegated). For now, record into THIS session's
            # cap_uses bucket keyed by the ancestor's audit_id — the
            # decision clock reads cap_uses for any audit_id, so the
            # accounting hits home regardless of which session holds
            # the ancestor cap (next-decision check walks the chain).
            cap_index = {c.audit_id: c for c in session.capability_set}
            parent_id = matched.parent_audit_id
            while parent_id is not None:
                parent = cap_index.get(parent_id)
                if parent is None:
                    break
                # Record using the parent's rate window so prune is
                # at the right horizon for that ancestor.
                if parent.rate_limit is not None:
                    await self._graph.record_cap_use(
                        session_id,
                        str(parent.audit_id),
                        dispatch_now,
                        prune_older_than=timedelta(
                            seconds=parent.rate_limit.window_seconds,
                        ),
                    )
                parent_id = parent.parent_audit_id

        context = ToolContext(session_id=session_id, label_set=session.label_set)
        # T104 — Pattern (3) ReferenceHandle bind step. AFTER decide()
        # approved, BEFORE the handler runs: substitute any handle-shaped
        # values in declared handle_arg_names with the store-bound real
        # value. Emits pattern3.handle_bind with the canonical
        # destination id (FR-047). Skipped when policy_context.handle_store
        # is None or the tool doesn't accept handles.
        bound_args = await self._bind_reference_handles(
            session_id=session_id,
            tool=tool,
            tool_name=tool_name,
            args=args,
        )
        try:
            result = await tool.handler(bound_args, context)
        except Exception as e:
            return ToolCallOutcome(
                decision=Decision.ALLOW,
                error=f"{type(e).__name__}: {e}",
                rule=None,
                tool_name=tool_name,
                tool_args=args,
            )

        await self._audit.write(
            Event(
                event_type=EventType.TOOL_RETURNED,
                session_id=session_id,
                payload={"tool": tool_name, "output": result.output},
            ),
        )

        # FR-025 raise-only inspector hook. Inspectors examine the
        # returned value + current axes and may return a delta. The
        # delta is composed via most_restrictive_inherit on AxisA/B,
        # which is monotone — inspectors can only RAISE taint, never
        # clear it. The runtime contract is structural: even a
        # buggy/malicious inspector that returns a lower-restriction
        # AxisA cannot lower the actual session axes.
        if self._policy_context is not None and self._policy_context.inspectors:
            await self._apply_inspectors(session, result.output)

        # Spec 004 P0 — DeclassifyingTransformer chain hook. Runs
        # AFTER inspectors so the declassifier sees any value the
        # inspector tainted. Each declassifier transforms the value
        # and emits a structural-proof. Crucially: declassifiers
        # reduce the PER-RESULT inherent_labels propagated to the
        # session, not the session's existing label_set (session
        # label growth stays monotonic — declassifier just adds
        # FEWER labels for this particular result).
        if self._policy_context is not None and self._policy_context.declassifiers:
            new_output, removed_labels = await self._apply_declassifiers(
                session_id,
                session,
                tool_name,
                result.output,
                tool.inherent_labels | result.additional_labels,
            )
            # Replace output with transformed value; subtract any
            # labels the declassifier "lowered away" from the set
            # that propagates this turn.
            result = _replace_tool_result(
                result,
                output=new_output,
                additional_labels=result.additional_labels - removed_labels,
            )
            tool_inherent_for_propagation = tool.inherent_labels - removed_labels
        else:
            tool_inherent_for_propagation = tool.inherent_labels

        # Spec 004 P0 FR-027/039 — per-arg payload labels. The tool
        # declares which arg values carry which labels; we add them
        # to this turn's propagation only when the corresponding arg
        # was actually populated. Lets tool authors say "body carries
        # confidential.personal" without painting EVERY email send
        # call regardless of body content.
        per_arg_labels = tool.extract_arg_inherent_labels(args)

        # Issue #35 — Custom-kind add_labels from servers.d/*.yaml.
        # When a tool with a custom CapabilityKind fires, the labels
        # declared in the yaml's `add_labels:` for that kind propagate
        # into the session. This closes the IFC story for plugin
        # kinds: declared destructiveness gates the call; declared
        # add_labels color the session afterward.
        from capabledeputy.policy.capabilities import kind_add_labels as _kind_labels

        custom_kind_labels = _kind_labels(tool.capability_kind)

        labels_to_add = (
            tool_inherent_for_propagation
            | result.additional_labels
            | per_arg_labels
            | custom_kind_labels
        )
        labels_added: frozenset[Label] = frozenset()
        if labels_to_add:
            before = session.label_set
            updated = await self._graph.add_labels(session_id, labels_to_add)
            labels_added = updated.label_set - before
            if labels_added:
                await self._audit.write(
                    Event(
                        event_type=EventType.LABEL_PROPAGATED,
                        session_id=session_id,
                        payload={
                            "labels_added": sorted(label.value for label in labels_added),
                            "source_tool": tool_name,
                        },
                    ),
                )

        return ToolCallOutcome(
            decision=Decision.ALLOW,
            output=result.output,
            labels_added=labels_added,
            tool_name=tool_name,
            tool_args=args,
            rule=policy_decision.rule,
            reason=policy_decision.reason,
        )

    async def _apply_decision_inspectors(
        self,
        session_id: Any,
        session: Any,
        action: Any,
        tool_name: str,
        proposed: Any,
    ) -> Any:
        """Run any registered DecisionInspectors on the proposed
        policy decision. Returns the (possibly adjusted) decision.

        Composes monotonically — TIGHTEN beats RELAX. Each inspector
        firing is audited so the trail captures the override origin.
        """
        if self._policy_context is None or not self._policy_context.decision_inspectors:
            return proposed

        from capabledeputy.substrate.decision_inspector_port import (
            compose_inspector_outcomes,
        )

        outcomes: list[tuple[str, Any]] = []
        for inspector in self._policy_context.decision_inspectors:
            try:
                oc = inspector.inspect(
                    action=action,
                    session=session,
                    proposed_outcome=proposed,
                )
            except Exception as e:
                # A buggy inspector must not crash the chokepoint —
                # treat as abstain + audit the failure for the operator.
                await self._audit.write(
                    Event(
                        event_type=EventType.POLICY_DECIDED,
                        session_id=session_id,
                        payload={
                            "tool": tool_name,
                            "decision_inspector_error": str(e),
                            "inspector": getattr(inspector, "name", "<unknown>"),
                        },
                    ),
                )
                continue
            if oc is not None:
                outcomes.append((getattr(inspector, "name", "<unknown>"), oc))

        composed = compose_inspector_outcomes(proposed.decision, outcomes)
        if composed is None:
            return proposed

        new_decision, rule_with_origin, rationale = composed
        from dataclasses import replace as _dc_replace

        adjusted = _dc_replace(
            proposed,
            decision=new_decision,
            rule=rule_with_origin,
            reason=rationale or proposed.reason,
        )
        # Dedicated DECISION_INSPECTOR_APPLIED event so auditors can
        # filter primitive applications separately from raw policy
        # decisions. Original POLICY_DECIDED event still fires at the
        # ordinary dispatch site reflecting the adjusted outcome.
        await self._audit.write(
            Event(
                event_type=EventType.DECISION_INSPECTOR_APPLIED,
                session_id=session_id,
                payload={
                    "tool": tool_name,
                    "applied_rule": rule_with_origin,
                    "original_decision": proposed.decision.value,
                    "adjusted_decision": new_decision.value,
                    "rationale": rationale,
                },
            ),
        )
        return adjusted

    async def _apply_declassifiers(
        self,
        session_id: Any,
        session: Any,
        tool_name: str,
        value: Any,
        candidate_labels: frozenset[Label],
    ) -> tuple[Any, frozenset[Label]]:
        """Run the declassifier chain on tool output. Returns
        (transformed_value, labels_to_remove_from_propagation).

        Each declassifier that fires:
          - Transforms the value (output becomes the chain's tail value)
          - Identifies labels to subtract from per-result propagation
            based on its `lower_axis_*` fields
          - Emits a DECLASSIFIER_APPLIED audit event with the diff +
            structural_proof_kind

        The session's label_set is NOT lowered — declassifier only
        reduces the labels propagated by THIS particular result. The
        chokepoint composition stays monotone.
        """
        if self._policy_context is None or not self._policy_context.declassifiers:
            return value, frozenset()

        from capabledeputy.substrate.declassifier_port import (
            apply_declassifier_chain,
        )

        try:
            final_value, applied = apply_declassifier_chain(
                tuple(self._policy_context.declassifiers),
                value=value,
                current_axis_a=getattr(session, "axis_a", None),
                current_axis_b=getattr(session, "axis_b", None),
                context={"tool": tool_name},
            )
        except Exception as e:
            # A buggy declassifier must not crash the chokepoint —
            # treat as no-op and surface the failure for the operator.
            await self._audit.write(
                Event(
                    event_type=EventType.DECLASSIFIER_APPLIED,
                    session_id=session_id,
                    payload={
                        "tool": tool_name,
                        "error": str(e),
                    },
                ),
            )
            return value, frozenset()

        # Emit one DECLASSIFIER_APPLIED per declassifier that fired.
        # Auditor sees the structural proof for each step.
        removed_labels: set[Label] = set()
        for result in applied:
            await self._audit.write(
                Event(
                    event_type=EventType.DECLASSIFIER_APPLIED,
                    session_id=session_id,
                    payload={
                        "tool": tool_name,
                        "structural_proof_kind": result.structural_proof_kind,
                        "audit_diff": result.audit_diff,
                        "lower_axis_a_categories": list(result.lower_axis_a_categories),
                        "lower_axis_b_level": result.lower_axis_b_level,
                    },
                ),
            )
            # Identify per-result Label members to drop from propagation
            # based on the declassifier's signals. The mapping is
            # operator-curated; here we apply a conservative default:
            # if a declassifier lowers pii to 'none', drop PII-bearing
            # labels from this result's propagation. Operators with
            # richer label hierarchies wire custom logic via the
            # declassifier's own implementation.
            for entry in result.lower_axis_a_categories:
                cat = entry.get("category", "").lower()
                to_tier = entry.get("to_tier", "").lower()
                if to_tier == "none":
                    # Drop labels whose value contains the category name
                    for label in candidate_labels:
                        if cat and cat in label.value.lower():
                            removed_labels.add(label)
            if result.lower_axis_b_level == "trusted":
                for label in candidate_labels:
                    if "untrusted" in label.value.lower():
                        removed_labels.add(label)

        return final_value, frozenset(removed_labels)

    async def _apply_inspectors(
        self,
        session: Any,
        value: object,
    ) -> None:
        """FR-025 — run every registered raise-only inspector against
        the just-returned value + current session axes; compose any
        returned taint into the session via most_restrictive_inherit.
        Refused to lower — the composition is monotone by construction."""
        from dataclasses import replace as dc_replace

        from capabledeputy.policy.labels import (
            most_restrictive_inherit_axis_a,
            most_restrictive_inherit_axis_b,
        )

        if self._policy_context is None:
            return
        new_axis_a = session.axis_a
        new_axis_b = session.axis_b
        for inspector in self._policy_context.inspectors:
            delta = inspector.inspect(
                value=value,
                current_axis_a=new_axis_a,
                current_axis_b=new_axis_b,
            )
            pre_a, pre_b = new_axis_a, new_axis_b
            new_axis_a = most_restrictive_inherit_axis_a(new_axis_a, delta.axis_a_raise)
            new_axis_b = most_restrictive_inherit_axis_b(new_axis_b, delta.axis_b_raise)
            # Audit each inspector that actually raised something. A
            # no-op inspector (delta with empty axes) doesn't fire an
            # event — keeps the audit stream signal-rich.
            if new_axis_a != pre_a or new_axis_b != pre_b:
                await self._audit.write(
                    Event(
                        event_type=EventType.INSPECTOR_APPLIED,
                        session_id=session.id,
                        payload={
                            "inspector": getattr(inspector, "__class__", type(inspector)).__name__,
                            "raised_axis_a": new_axis_a != pre_a,
                            "raised_axis_b": new_axis_b != pre_b,
                        },
                    ),
                )
        if new_axis_a != session.axis_a or new_axis_b != session.axis_b:
            updated = dc_replace(session, axis_a=new_axis_a, axis_b=new_axis_b)
            await self._graph._save(updated)
            self._graph._sessions[session.id] = updated

    def _build_v2_decide_kwargs(
        self,
        session: Any,
        tool: ToolDefinition,
    ) -> dict[str, Any]:
        """Build the kw-only v2 args for engine.decide() from the
        session axes + tool definition + policy context. When the
        policy context is absent, returns an empty dict (the v2 leg
        stays dormant; legacy behavior preserved). When override_grants
        is present, threads it + session_id so an active grant
        short-circuits to ALLOW (Demo #2 / T079)."""
        if self._policy_context is None:
            return {}
        kwargs: dict[str, Any] = {}
        if tool.effect_class is not None:
            kwargs["axis_a"] = session.axis_a
            kwargs["axis_b"] = session.axis_b
            kwargs["axis_d"] = session.axis_d
            kwargs["effect_class"] = tool.effect_class
            kwargs["rules_v2"] = self._policy_context.rules_v2
        if self._policy_context.override_grants is not None:
            kwargs["override_grants"] = self._policy_context.override_grants
            kwargs["session_id"] = session.id
        if self._policy_context.bindings is not None:
            # Per-purpose binding composition (003 follow-on):
            # if the session has a purpose_handle resolving to a
            # Purpose with non-empty .bindings, compose them with
            # the global BindingSet. Specificity-based resolution
            # picks the most-specific match for category + tier;
            # purpose paths typically beat global wildcards naturally.
            effective_bindings = self._policy_context.bindings
            ph = getattr(session, "purpose_handle", None)
            if ph and self._policy_context.purposes is not None:
                purpose = self._policy_context.purposes.get(ph)
                if purpose is not None and purpose.bindings:
                    from capabledeputy.policy.bindings import BindingSet

                    effective_bindings = BindingSet(
                        bindings=(*self._policy_context.bindings.bindings, *purpose.bindings),
                    )
            kwargs["bindings"] = effective_bindings
        # T094 / Demo #4 — derive effective reversibility from the
        # tool's default_reversibility (when declared). The binding's
        # mutability composition lands in a follow-up; today the
        # tool's declaration is authoritative.
        if tool.default_reversibility is not None:
            import contextlib

            with contextlib.suppress(KeyError, ValueError):
                kwargs["effective_reversibility"] = ReversibilityLabel.from_dict(
                    tool.default_reversibility,
                )
        # Sub-phases F/H — envelope dial + clearance/floor.
        if self._policy_context.envelope_set is not None:
            kwargs["envelope_set"] = self._policy_context.envelope_set
        if self._policy_context.risk_preference is not None:
            kwargs["risk_preference"] = self._policy_context.risk_preference
        # Per-session profile derivation (FR-008 BLP + FR-004 Biba).
        # If the session declares a clearance_profile_id, look up the
        # profile and use its max_tier / integrity_floor_level. This
        # is the wiring that makes BLP/Biba runtime-active. The
        # context-level fallback (clearance_max_tier kwarg) still
        # works for single-tenant configurations.
        derived_clearance = None
        derived_floor = None
        if getattr(session, "clearance_profile_id", None) and self._policy_context.profiles:
            profile = self._policy_context.profiles.get(session.clearance_profile_id)
            if profile is not None:
                derived_clearance = getattr(profile, "max_tier", None)
                derived_floor = getattr(profile, "integrity_floor_level", None)
        clearance = derived_clearance or self._policy_context.clearance_max_tier
        floor = derived_floor or self._policy_context.integrity_floor_level
        if clearance is not None:
            kwargs["clearance_max_tier"] = clearance
        if floor is not None:
            kwargs["integrity_floor_level"] = floor
        if self._policy_context.risk_register is not None:
            kwargs["risk_register"] = self._policy_context.risk_register
        kwargs["sandbox_actuator_wired"] = self._policy_context.sandbox_actuator_wired
        return kwargs

    async def _bind_reference_handles(
        self,
        *,
        session_id: UUID,
        tool: ToolDefinition,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """T104 — substitute Pattern (3) handle ids with real values
        from the store; emit pattern3.handle_bind per substitution.
        Returns the substituted args dict. If no policy_context or no
        handle_store wired, returns `args` unchanged. The bind() call
        itself enforces unforgeable / per-session / non-empty-
        destination invariants (FR-047)."""
        if (
            self._policy_context is None
            or self._policy_context.handle_store is None
            or not tool.accepts_handles
            or not tool.handle_arg_names
        ):
            return args
        store = self._policy_context.handle_store
        substituted: dict[str, Any] = dict(args)
        for arg_name in tool.handle_arg_names:
            raw = substituted.get(arg_name)
            if not isinstance(raw, str) or not is_planner_safe_token(raw):
                continue
            try:
                handle_id = UUID(raw)
            except ValueError:
                continue
            # Best-effort destination canonical id: the tool's
            # target_arg if surfaces_destination_id is declared; else
            # tool name. The full T012 path is to consult a port — that
            # lands when source_port has providers (spec 004).
            dest = (
                str(substituted.get(tool.target_arg, ""))
                if tool.surfaces_destination_id
                else f"tool:{tool_name}"
            )
            audit_id = uuid4()
            try:
                value = store.bind(
                    session_id=session_id,
                    handle_id=handle_id,
                    destination_canonical_id=dest or f"tool:{tool_name}",
                    tool=tool_name,
                    audit_id=audit_id,
                )
            except ReferenceHandleError:
                # Forged / cross-session / empty-destination: refuse
                # the substitution; the handler sees the raw token, the
                # underlying call typically fails — fail-loud not
                # fail-silent.
                continue
            substituted[arg_name] = value
            await self._audit.write(
                Event(
                    audit_id=audit_id,
                    event_type=EventType.PATTERN3_HANDLE_BIND,
                    session_id=session_id,
                    payload={
                        "handle_id": str(handle_id),
                        "tool": tool_name,
                        "arg_name": arg_name,
                        "destination_canonical_id": dest or f"tool:{tool_name}",
                    },
                ),
            )
        return substituted

    async def _register_approval(
        self,
        session_id: UUID,
        labels_in: frozenset[Label],
        submission: dict[str, Any],
    ) -> int:
        """Submit the resolved approval to the queue and return its id.

        Dedups: if an identical pending request already exists for this
        session (same action + target + payload), return that id rather
        than spawning a duplicate — the agent loop can re-attempt the
        same gated call within a turn.
        """
        from capabledeputy.approval.model import ApprovalAction, ApprovalStatus

        action = ApprovalAction(submission["action"])
        target = submission["target"]
        payload = submission["payload"]

        for existing in self._approval_queue.list(status=ApprovalStatus.PENDING):
            if (
                existing.from_session == session_id
                and existing.action == action
                and existing.target == target
                and existing.payload == payload
            ):
                return existing.id

        request = await self._approval_queue.submit(
            from_session=session_id,
            action=action,
            payload=payload,
            target=target,
            labels_in=labels_in,
            justification=submission.get("justification", ""),
        )
        return request.id

    async def _emit_policy_decision(
        self,
        session_id: UUID,
        tool_name: str,
        args: dict[str, Any],
        decision: PolicyDecision,
        tool: ToolDefinition,
    ) -> None:
        await self._audit.write(
            Event(
                event_type=EventType.POLICY_DECIDED,
                session_id=session_id,
                payload=build_policy_decided_payload(tool_name, args, decision),
            ),
        )
        if decision.refused_relax_inputs:
            await self._audit.write(
                Event(
                    event_type=EventType.RELAXATION_REFUSED,
                    session_id=session_id,
                    payload=build_relaxation_refused_payload(
                        tool_name,
                        args,
                        decision,
                    ),
                ),
            )
        # T092 / FR-016 — emit RESIDUAL_RISK_EXCEPTION when an ALLOW
        # decision crosses an operator-declared risk threshold. Pulls
        # risk_ids from any matched_capability + matched v2 rules.
        if (
            self._policy_context is not None
            and self._policy_context.residual_risk_thresholds is not None
            and decision.decision is Decision.ALLOW
        ):
            decision_risk_ids: list[str] = []
            if decision.matched_capability is not None:
                # Capability today has no direct risk_ids field; the
                # T012 risk_ids declaration on the originating tool is
                # the authoritative source. Audit captures both layers
                # in a follow-up.
                pass
            decision_risk_ids.extend(tool.risk_ids)
            signal = should_emit_residual_risk(
                decision_is_allow=True,
                decision_risk_ids=tuple(decision_risk_ids),
                thresholds=self._policy_context.residual_risk_thresholds,
            )
            if signal.should_emit:
                await self._audit.write(
                    Event(
                        event_type=EventType.RESIDUAL_RISK_EXCEPTION,
                        session_id=session_id,
                        payload={
                            "tool": tool_name,
                            "args": args,
                            "decision_rule": decision.rule,
                            "crossed_risk_ids": list(signal.crossed),
                            "non_suppressible": True,
                        },
                    ),
                )

    async def _emit_capability_checked(
        self,
        session_id: UUID,
        action: Action,
        decision: PolicyDecision,
    ) -> None:
        from capabledeputy.policy.capabilities import kind_name

        await self._audit.write(
            Event(
                event_type=EventType.CAPABILITY_CHECKED,
                session_id=session_id,
                payload={
                    "kind": kind_name(action.kind),
                    "target": action.target,
                    "amount": action.amount,
                    "matched": decision.matched_capability is not None,
                    "matched_audit_id": (
                        str(decision.matched_capability.audit_id)
                        if decision.matched_capability
                        else None
                    ),
                },
            ),
        )
