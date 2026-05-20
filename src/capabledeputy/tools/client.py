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
    sandbox_actuator_wired: bool = False
    # FR-025 raise-only inspectors. Run on every tool return so any
    # taint the inspector identifies is added to the session's axes.
    # Composition is monotone (most_restrictive_inherit); inspectors
    # cannot CLEAR taint, only RAISE it.
    inspectors: tuple[Any, ...] = ()
    # 003 runtime activation — Profiles registry keyed by profile_id.
    # When a session has clearance_profile_id set, the dispatcher
    # derives per-session clearance_max_tier (FR-008 BLP) +
    # integrity_floor_level (FR-004 Biba) from the matching profile.
    # Without this, BLP/Biba are library-only and never fire.
    profiles: dict[str, Any] = field(default_factory=dict)


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
        policy_decision = decide(
            session.label_set,
            session.capability_set,
            action,
            used_kinds=session.used_kinds,
            now=dispatch_now,
            cap_uses=session.cap_uses,
            **v2_kwargs,
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

        labels_to_add = tool.inherent_labels | result.additional_labels
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
            new_axis_a = most_restrictive_inherit_axis_a(new_axis_a, delta.axis_a_raise)
            new_axis_b = most_restrictive_inherit_axis_b(new_axis_b, delta.axis_b_raise)
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
            kwargs["bindings"] = self._policy_context.bindings
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
        await self._audit.write(
            Event(
                event_type=EventType.CAPABILITY_CHECKED,
                session_id=session_id,
                payload={
                    "kind": action.kind.value,
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
