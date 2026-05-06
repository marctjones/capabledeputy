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


@dataclass(frozen=True)
class ToolCallOutcome:
    decision: Decision
    output: dict[str, Any] | None = None
    rule: str | None = None
    reason: str | None = None
    labels_added: frozenset[Label] = field(default_factory=frozenset)
    error: str | None = None


class LabeledToolClient:
    def __init__(
        self,
        registry: ToolRegistry,
        graph: SessionGraph,
        audit: AuditWriter,
    ) -> None:
        self._registry = registry
        self._graph = graph
        self._audit = audit

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
        policy_decision = decide(session.label_set, session.capability_set, action)

        await self._emit_policy_decision(session_id, tool_name, args, policy_decision)
        await self._emit_capability_checked(session_id, action, policy_decision)

        if policy_decision.decision != Decision.ALLOW:
            return ToolCallOutcome(
                decision=policy_decision.decision,
                rule=policy_decision.rule,
                reason=policy_decision.reason,
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

        context = ToolContext(session_id=session_id, label_set=session.label_set)
        try:
            result = await tool.handler(args, context)
        except Exception as e:
            return ToolCallOutcome(
                decision=Decision.ALLOW,
                error=f"{type(e).__name__}: {e}",
                rule=None,
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
        )

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
                payload={
                    "tool": tool_name,
                    "args": args,
                    "decision": decision.decision.value,
                    "rule": decision.rule,
                    "reason": decision.reason,
                    "effective_labels": sorted(
                        label.value for label in decision.effective_labels
                    ),
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
