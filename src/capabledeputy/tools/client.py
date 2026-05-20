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
from uuid import UUID

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.actions import Action
from capabledeputy.policy.engine import PolicyDecision, decide
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.registry import ToolContext, ToolRegistry


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
    ) -> None:
        self._registry = registry
        self._graph = graph
        self._audit = audit
        # Optional so unit tests can construct the client without the
        # full App. When present, REQUIRE_APPROVAL outcomes are
        # registered in the queue here, at the policy chokepoint —
        # not by whichever client happens to be driving the session.
        self._approval_queue = approval_queue

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
        policy_decision = decide(
            session.label_set,
            session.capability_set,
            action,
            used_kinds=session.used_kinds,
            now=dispatch_now,
            cap_uses=session.cap_uses,
        )

        await self._emit_policy_decision(session_id, tool_name, args, policy_decision)
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
        try:
            result = await tool.handler(args, context)
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
        )

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
