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
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.actions import Action
from capabledeputy.policy.assurance import (
    should_emit_residual_risk,
)
from capabledeputy.policy.capabilities import CapabilityKind, kind_name
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.labels import LabelState, ProvenanceLevel
from capabledeputy.policy.overrides import (
    TrustProfile,
)
from capabledeputy.policy.pipeline import (
    DecisionRequest,
    DefaultPolicyPipeline,
    PolicyPipeline,
)
from capabledeputy.policy.reversibility import ReversibilityLabel
from capabledeputy.policy.rules import Decision
from capabledeputy.provenance import (
    ProvenanceRecorder,
    capability_node_id,
    stable_digest,
    tool_result_node_id,
)
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.policy_hooks import ToolPolicyHooks
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolRegistry
from capabledeputy.tools.source_flow import ToolSourceFlow

if TYPE_CHECKING:
    from capabledeputy.policy.context import PolicyContext


_PROVENANCE_INTEGRITY_RANK: dict[ProvenanceLevel, int] = {
    ProvenanceLevel.PRINCIPAL_DIRECT: 0,
    ProvenanceLevel.SYSTEM_INTERNAL: 1,
    ProvenanceLevel.EXTERNAL_UNTRUSTED: 2,
}
_INTEGRITY_FLOOR_RANK: dict[str, int] = {
    ProvenanceLevel.EXTERNAL_UNTRUSTED.value: 0,
    ProvenanceLevel.SYSTEM_INTERNAL.value: 1,
    ProvenanceLevel.PRINCIPAL_DIRECT.value: 2,
}
_EGRESS_LIKE_KINDS: frozenset[CapabilityKind] = frozenset(
    {
        CapabilityKind.SEND_EMAIL,
        CapabilityKind.SEND_MESSAGE,
        CapabilityKind.GMAIL_DRAFT,
        CapabilityKind.APPLE_MAIL_DRAFT,
        CapabilityKind.CALENDAR_WRITE,
        CapabilityKind.CREATE_CAL,
        CapabilityKind.MODIFY_CAL,
        CapabilityKind.DELETE_CAL,
        CapabilityKind.QUEUE_PURCHASE,
        CapabilityKind.WEB_FETCH,
        CapabilityKind.BROWSER_NAVIGATE,
        CapabilityKind.BROWSER_INTERACT,
        CapabilityKind.BROWSER_SCRIPT,
        CapabilityKind.BROWSER_FILE,
        CapabilityKind.MACOS_APP_CONTROL,
        CapabilityKind.MACOS_CLIPBOARD_WRITE,
        CapabilityKind.PAGES_EDIT,
        CapabilityKind.PAGES_EXPORT,
        CapabilityKind.NUMBERS_EDIT,
        CapabilityKind.NUMBERS_EXPORT,
        CapabilityKind.WRITE_FS,
        CapabilityKind.CREATE_FS,
        CapabilityKind.MODIFY_FS,
        CapabilityKind.DELETE_FS,
    }
)


def _replace_tool_result(
    original: Any,
    *,
    output: Any = None,
    additional_tags: LabelState | None = None,
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
    if additional_tags is not None:
        kwargs["additional_tags"] = additional_tags
    return _dc_replace(original, **kwargs)


def build_policy_decided_payload(
    tool_name: str,
    args: dict[str, Any],
    decision: PolicyDecision,
) -> dict[str, Any]:
    """Construct the JSON payload for a `policy.decided` audit event.

    The base payload is the stable audit surface consumed by existing readers.
    When the decision was reached with the v2 (003 US2) axis-based
    evaluator in play, two additional fields land in the payload:
    `v2_outcome` (the RuleOutcome the evaluator produced — AUTO/
    SUGGEST/REQUIRE_APPROVAL/DENY) and `v2_matched_rule_ids` (the
    sorted ids of human-ratified rules that matched). Together these
    give T041 audit-reconstruction enough to replay the v2 leg of
    the composition (FR-021).

    Omitted entirely when no v2 evaluation ran so non-axis decisions keep the
    compact base payload.
    """
    payload: dict[str, Any] = {
        "tool": tool_name,
        "args": args,
        "decision": decision.decision.value,
        "rule": decision.rule,
        "reason": decision.reason,
    }
    if decision.v2_outcome is not None:
        payload["v2_outcome"] = decision.v2_outcome.value
        payload["v2_matched_rule_ids"] = list(decision.v2_matched_rule_ids)
    if decision.refused_relax_inputs:
        payload["refused_relax_inputs"] = [
            {"description": r.description, "origin": r.origin}
            for r in decision.refused_relax_inputs
        ]
    # T048 — full Label-State/D snapshot when v2 ran. Enough for T041
    # audit-reconstruction to rebuild LabelState/D from the payload and
    # replay evaluate() to the same outcome (FR-021).
    if decision.labels_snapshot is not None:
        payload["label_state"] = decision.labels_snapshot.to_dict()
    if decision.axis_d_snapshot is not None:
        payload["axis_d"] = decision.axis_d_snapshot.to_dict()
    if decision.effect_class is not None:
        payload["effect_class"] = decision.effect_class
    payload["policy_trace"] = {
        "tool": tool_name,
        "args": args,
        "decision": decision.decision.value,
        "rule": decision.rule,
        "matched_capability_audit_id": (
            str(decision.matched_capability.audit_id)
            if decision.matched_capability is not None
            else None
        ),
        "matched_capability_kind": (
            kind_name(decision.matched_capability.kind)
            if decision.matched_capability is not None
            else None
        ),
        "matched_capability_pattern": (
            decision.matched_capability.pattern if decision.matched_capability is not None else None
        ),
        "effect_class": decision.effect_class,
        "v2_outcome": decision.v2_outcome.value if decision.v2_outcome else None,
        "v2_matched_rule_ids": list(decision.v2_matched_rule_ids),
    }
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
    tags_added: LabelState = field(default_factory=LabelState)
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
        policy_pipeline: PolicyPipeline | None = None,
    ) -> None:
        self._registry = registry
        self._graph = graph
        self._audit = audit
        # Optional so unit tests can construct the client without the
        # full App. When present, REQUIRE_APPROVAL outcomes are
        # registered in the queue here, at the policy chokepoint —
        # not by whichever client happens to be driving the session.
        self._approval_queue = approval_queue
        # When provided, the four-axis decision pipeline activates. When
        # absent, dispatch uses the base capability decision path.
        self._policy_context = policy_context
        self._policy_pipeline = policy_pipeline or DefaultPolicyPipeline()
        self._source_flow = ToolSourceFlow(policy_context=policy_context, audit=audit)
        self._provenance = ProvenanceRecorder(audit)
        self._policy_hooks = ToolPolicyHooks(
            policy_context=policy_context,
            audit=audit,
            graph=graph,
        )

    @property
    def policy_context(self) -> PolicyContext | None:
        """Read-only accessor so callers (e.g. the agent loop building
        an LLM-context summary) don't have to reach into a private."""
        return self._policy_context

    def update_policy_context(self, policy_context: PolicyContext | None) -> None:
        """Refresh daemon-owned policy context after validated config edits."""
        self._policy_context = policy_context
        self._source_flow = ToolSourceFlow(policy_context=policy_context, audit=self._audit)
        self._policy_hooks = ToolPolicyHooks(
            policy_context=policy_context,
            audit=self._audit,
            graph=self._graph,
        )

    def _purpose_contamination_categories(self, session_id: UUID) -> tuple[str, ...]:
        if self._policy_context is None or self._policy_context.purposes is None:
            return ()
        session = self._graph.get(session_id)
        purpose_handle = getattr(session, "purpose_handle", "unset")
        categories = sorted(tag.category for tag in session.label_state.a)
        return tuple(
            category
            for category in categories
            if not self._policy_context.purposes.admits(purpose_handle, category)
        )

    async def _emit_purpose_contamination_residual(
        self,
        session_id: UUID,
        tool_name: str,
        args: dict[str, Any],
        action: Action,
        decision: PolicyDecision,
    ) -> None:
        if decision.decision != Decision.ALLOW:
            return
        if action.kind in _EGRESS_LIKE_KINDS:
            return
        inadmissible = self._purpose_contamination_categories(session_id)
        if not inadmissible:
            return
        session = self._graph.get(session_id)
        await self._audit.write(
            Event(
                event_type=EventType.PURPOSE_CONTAMINATION_SUSPECTED,
                session_id=session_id,
                payload={
                    "tool": tool_name,
                    "args": args,
                    "purpose_handle": session.purpose_handle,
                    "inadmissible_categories": list(inadmissible),
                    "decision_rule": decision.rule,
                    "decision_reason": decision.reason,
                },
            ),
        )

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
        from capabledeputy.policy.labels import most_restrictive_inherit

        source_tags = self._source_flow.extract_source_tags(
            session_id=session_id,
            tool=tool,
            args=args,
        )
        # One authoritative decision clock per dispatch — resolved here
        # at the chokepoint, threaded into decide() AND reused as the
        # recorded use timestamp so the rate-limit window is consistent.
        dispatch_now = datetime.now(UTC)
        v2_kwargs = self._build_v2_decide_kwargs(session, tool, action=action)
        # 002 US2: pass the session's revoked_audit_ids so any
        # capability inert under cascade is denied at decide time.
        # Default-tolerant: pre-002 sessions deserialize with the
        # empty set, which is a no-op here.
        # R4b.2 — bundled LabelState as canonical Axis A/B input. When
        # v2_kwargs includes labels, use that (from _build_v2_decide_kwargs);
        # otherwise pass the session's label_state.
        decide_labels = v2_kwargs.get("labels", session.label_state)
        if source_tags != LabelState():
            decide_labels = most_restrictive_inherit(decide_labels, source_tags)
            v2_kwargs["labels"] = decide_labels
        decision_request = DecisionRequest(
            capabilities=session.capability_set,
            action=action,
            used_kinds=session.used_kinds,
            now=dispatch_now,
            cap_uses=session.cap_uses,
            labels=decide_labels,
            revoked_audit_ids=getattr(session, "revoked_audit_ids", frozenset()),
            # Cookbook §4 #6 — opt-in first-action-of-kind prompt.
            # The session carries the flag; the engine fires the
            # REQUIRE_APPROVAL escalation only when on.
            first_use_prompt_enabled=getattr(
                session,
                "first_use_prompt_enabled",
                False,
            ),
            # Cookbook P2.6 — rate-limit-as-friction. Cautious
            # sessions keep the hard DENY (rate limit is a non-
            # negotiable floor). Balanced/aggressive sessions get
            # REQUIRE_APPROVAL on overflow so the operator can vouch
            # mid-stream and catch runaway loops. The session's
            # risk_preference_at_spawn drives the toggle.
            rate_limit_escalation=getattr(
                session,
                "risk_preference_at_spawn",
                "cautious",
            )
            != "cautious",
            **{k: v for k, v in v2_kwargs.items() if k != "labels"},
        )
        policy_decision = self._policy_pipeline.decide(decision_request).decision

        # DecisionInspector hook: registered inspectors run AFTER the
        # standard decision and may relax (loosen) or tighten
        # (strengthen) the outcome. Tighten beats Relax; non-monotone
        # moves are rejected at composition time.
        policy_decision = await self._policy_hooks.apply_decision_inspectors(
            session_id,
            session,
            action,
            tool_name,
            policy_decision,
            v2_kwargs.get("effective_reversibility"),
            effect_class=tool.effect_class,
        )

        # Cookbook Pattern ⑥ — shadow-mode rewrite. When the session
        # is in SHADOW enforcement, non-ALLOW outcomes are rewritten
        # to ALLOW and a POLICY_SHADOWED audit event is emitted
        # carrying the original decision so the operator can review
        # what STRICT would have done. Capability-structural denies
        # (no matching cap) are NOT rewritten — those are missing
        # authority, not contested rule outcomes. The check is the
        # rule field: any policy_decision with a real rule attached
        # but a non-ALLOW outcome is shadow-eligible.
        policy_decision = await self._policy_hooks.maybe_shadow_rewrite(
            session_id,
            session,
            tool_name,
            action,
            policy_decision,
        )

        if policy_decision.decision == Decision.ALLOW:
            source_floor = self._source_flow.restricted_source_floor_decision(
                tool=tool,
                source_tags=source_tags,
                base_decision=policy_decision,
                labels_snapshot=decide_labels,
                axis_d_snapshot=v2_kwargs.get("axis_d"),
            )
            if source_floor is not None:
                policy_decision = source_floor

        await self._emit_policy_decision(session_id, tool_name, args, policy_decision, tool)
        await self._emit_purpose_contamination_residual(
            session_id,
            tool_name,
            args,
            action,
            policy_decision,
        )
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
                        session.label_state,
                        approval_submission,
                        rule=policy_decision.rule,
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

        handle_store = self._policy_context.handle_store if self._policy_context else None
        context = ToolContext(
            session_id=session_id,
            label_state=session.label_state,
            handle_store=handle_store,
        )
        # T104 — Pattern (3) ReferenceHandle bind step. AFTER decide()
        # approved, BEFORE the handler runs: substitute any handle-shaped
        # values in declared handle_arg_names with the store-bound real
        # value. Emits pattern3.handle_bind with the canonical
        # destination id (FR-047). Skipped when policy_context.handle_store
        # is None or the tool doesn't accept handles.
        (
            bound_args,
            bound_source_tags,
            reference_parent_nodes,
        ) = await self._source_flow.bind_reference_handles(
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

        tool_returned_event = Event(
            audit_id=uuid4(),
            event_type=EventType.TOOL_RETURNED,
            session_id=session_id,
            payload={"tool": tool_name, "output": result.output},
        )
        await self._audit.write(tool_returned_event)

        # FR-025 raise-only inspector hook. Inspectors examine the
        # returned value + current label_state and may return a delta.
        # The delta is composed via the directional `inherit` on
        # LabelState, which is monotone — inspectors can only RAISE
        # taint, never clear it. The runtime contract is structural:
        # even a buggy/malicious inspector that returns a
        # lower-restriction LabelState cannot lower the session's taint.
        await self._policy_hooks.apply_inspectors(session, result.output)

        # Spec 004 P0 — DeclassifyingTransformer chain hook. Runs
        # AFTER inspectors so the declassifier sees any value the
        # inspector tainted. Each declassifier transforms the value
        # and emits a structural-proof. Crucially: declassifiers
        # reduce the PER-RESULT inherent_tags propagated to the
        # session, not the session's existing label_state (session
        # label growth stays monotonic — declassifier just adds
        # FEWER tags for this particular result).
        new_output, tags_to_remove = await self._policy_hooks.apply_declassifiers(
            session_id,
            session,
            tool_name,
            result.output,
            tool.inherent_tags,
            result.additional_tags,
        )
        # Replace output with transformed value; subtract any tags the
        # declassifier "lowered away" from the set that propagates this
        # turn — from BOTH the tool's inherent_tags AND the result's
        # additional_tags (F9 fix). Most real taint (fs reads, the fs/
        # email labelers) arrives via additional_tags; removing only
        # from inherent left it undeclassifiable.
        from capabledeputy.policy.labels import _remove

        result = _replace_tool_result(
            result,
            output=new_output,
            additional_tags=_remove(result.additional_tags, tags_to_remove),
        )
        tool_inherent_for_propagation = _remove(tool.inherent_tags, tags_to_remove)

        # Spec 004 P0 FR-027/039 — per-arg payload tags. The tool
        # declares which arg values carry which tags; we add them
        # to this turn's propagation only when the corresponding arg
        # was actually populated. Lets tool authors say "body carries
        # personal category" without painting EVERY email send
        # call regardless of body content.
        per_arg_tags = tool.extract_arg_inherent_tags(args)

        # Issue #35 — Custom-kind add_tags from servers.d/*.yaml.
        # When a tool with a custom CapabilityKind fires, the tags
        # declared in the yaml's `add_tags:` for that kind propagate
        # into the session. This closes the IFC story for plugin
        # kinds: declared destructiveness gates the call; declared
        # add_tags color the session afterward.
        from capabledeputy.policy.capabilities import kind_add_tags as _kind_tags

        custom_kind_tags = _kind_tags(tool.capability_kind)

        # Compose all four-axis taint sources via most_restrictive_inherit
        from capabledeputy.policy.labels import most_restrictive_inherit

        tags_to_add = most_restrictive_inherit(
            tool_inherent_for_propagation,
            result.additional_tags,
            per_arg_tags,
            custom_kind_tags,
            bound_source_tags,
        )
        tags_added: LabelState = LabelState()
        if tags_to_add != LabelState():
            before = session.label_state
            await self._graph.add_tags(session_id, tags_to_add)
            updated = self._graph.get(session_id)
            tags_added = updated.label_state
            if tags_added != before:
                # Emit audit with four-axis delta categories/levels
                categories = sorted(t.category for t in tags_added.a)
                levels = sorted(t.level.value for t in tags_added.b)
                await self._audit.write(
                    Event(
                        event_type=EventType.LABEL_PROPAGATED,
                        session_id=session_id,
                        payload={
                            "categories_added": categories,
                            "levels_added": levels,
                            "source_tool": tool_name,
                        },
                    ),
                )

        tool_node_id = tool_result_node_id(tool_returned_event.audit_id)
        await self._provenance.node(
            session_id=session_id,
            node_id=tool_node_id,
            kind="tool_result",
            materialized_id=f"tool:{tool_name}:{tool_returned_event.audit_id}",
            label_state=tags_to_add,
            event_audit_id=tool_returned_event.audit_id,
            metadata={
                "tool": tool_name,
                "target": str(action.target),
                "output_digest": stable_digest(result.output),
            },
        )
        if matched is not None:
            cap_node_id = capability_node_id(matched.audit_id)
            await self._provenance.node(
                session_id=session_id,
                node_id=cap_node_id,
                kind="capability",
                materialized_id=f"capability:{matched.audit_id}",
                metadata={
                    "kind": kind_name(matched.kind),
                    "pattern": matched.pattern,
                    "origin": matched.origin.value,
                },
            )
            await self._provenance.edge(
                session_id=session_id,
                from_node_id=cap_node_id,
                to_node_id=tool_node_id,
                kind="authorized",
                event_audit_id=tool_returned_event.audit_id,
            )
        for parent_node_id in reference_parent_nodes:
            await self._provenance.node(
                session_id=session_id,
                node_id=parent_node_id,
                kind="reference_handle",
                materialized_id=parent_node_id,
            )
            await self._provenance.edge(
                session_id=session_id,
                from_node_id=parent_node_id,
                to_node_id=tool_node_id,
                kind="input",
                event_audit_id=tool_returned_event.audit_id,
            )

        return ToolCallOutcome(
            decision=Decision.ALLOW,
            output=result.output,
            tags_added=tags_added,
            tool_name=tool_name,
            tool_args=args,
            rule=policy_decision.rule,
            reason=policy_decision.reason,
        )

    def _resolve_action_axis_d(self, session_axis_d: Any, *, action: Any) -> Any:
        """Compose the action-time axis_d by resolving the action's
        target against the wired RelationshipGroups. The session's
        existing axis_d is preserved; only `relationship_group_ids`
        is widened by the resolution.

        No-op when no RelationshipGroups is wired or the action has
        no target — returns `session_axis_d` unchanged."""
        if self._policy_context is None or self._policy_context.relationship_groups is None:
            return session_axis_d
        target = getattr(action, "target", None)
        if not target:
            return session_axis_d
        resolved = self._policy_context.relationship_groups.resolve_target(str(target))
        if not resolved:
            return session_axis_d
        # Merge with whatever the session already carries — don't
        # drop existing memberships, only widen.
        existing = getattr(session_axis_d, "relationship_group_ids", frozenset())
        merged = frozenset(existing) | resolved
        if merged == existing:
            return session_axis_d
        from dataclasses import replace as _dc_replace

        return _dc_replace(session_axis_d, relationship_group_ids=merged)

    def _build_v2_decide_kwargs(
        self,
        session: Any,
        tool: ToolDefinition,
        *,
        action: Any | None = None,
    ) -> dict[str, Any]:
        """Build the kw-only v2 args for engine.decide() from the
        session axes + tool definition + policy context. When the
        policy context is absent, returns an empty dict and the axis-aware
        decision leg stays dormant. When override_grants
        is present, threads it + session_id so an active grant
        short-circuits to ALLOW (Demo #2 / T079)."""
        if self._policy_context is None:
            return {}
        kwargs: dict[str, Any] = {}
        if tool.effect_class is not None:
            # R4b.4 (done) — the session's single `label_state` field is
            # the canonical Axis A/B input to the engine.
            kwargs["labels"] = session.label_state
            # Cookbook P2.3 — merge resolved counterparty groups into
            # axis_d.relationship_group_ids when a RelationshipGroups
            # registry is wired. Without this, even an explicit
            # `family` group containing spouse@x.com would not affect
            # decisions about sends to spouse@x.com — the rules check
            # axis_d.relationship_group_ids, which is session-wide by
            # default. The per-action resolution gives the
            # family-personal-email-suggest rule (and similar) the
            # signal it actually needs.
            kwargs["axis_d"] = self._resolve_action_axis_d(
                session.axis_d,
                action=action,
            )
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
        operation_floor = self._operation_integrity_floor(tool)
        if operation_floor is not None:
            floor = self._stricter_integrity_floor(floor, operation_floor.value)
        if clearance is not None:
            kwargs["clearance_max_tier"] = clearance
        if floor is not None:
            kwargs["integrity_floor_level"] = floor
        if self._policy_context.risk_register is not None:
            kwargs["risk_register"] = self._policy_context.risk_register
        kwargs["sandbox_actuator_wired"] = self._policy_context.sandbox_actuator_wired
        kwargs["devbox_manager_wired"] = self._policy_context.devbox_manager_wired
        kwargs["egress_override_categories"] = self._policy_context.egress_override_categories
        kwargs["egress_override_tiers"] = self._policy_context.egress_override_tiers
        # Slice C (FR-049) — the `personal` trust profile lets a human-
        # ratified `crosses_floor` rule cross a structural floor over the
        # operator's own data. Read off the loaded Override Policy.
        op = self._policy_context.override_policies
        if op is not None:
            kwargs["trust_profile_is_personal"] = op.trust_profile is TrustProfile.PERSONAL
        return kwargs

    def _operation_integrity_floor(self, tool: ToolDefinition) -> ProvenanceLevel | None:
        floors = [op.required_floor for op in tool.operations if op.required_floor is not None]
        if not floors:
            return None

        return min(floors, key=lambda level: _PROVENANCE_INTEGRITY_RANK[level])

    def _stricter_integrity_floor(self, existing: str | None, candidate: str) -> str:
        if existing is None:
            return candidate

        return max((existing, candidate), key=lambda level: _INTEGRITY_FLOOR_RANK.get(level, 999))

    async def _register_approval(
        self,
        session_id: UUID,
        label_state: LabelState,
        submission: dict[str, Any],
        rule: str | None = None,
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
            labels_in=label_state,
            justification=submission.get("justification", ""),
            rule=rule,
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
