"""Policy hook lifecycle support for tool dispatch."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from inspect import isawaitable
from types import SimpleNamespace
from typing import Any

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.labels import LabelState, ProvenanceLevel, inherit
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.model import EnforcementMode
from capabledeputy.substrate.decision_inspector_port import (
    compose_inspector_outcomes,
    is_strictly_less_restrictive,
)
from capabledeputy.substrate.declassifier_port import apply_declassifier_chain


class ToolPolicyHooks:
    """Runs operator extension hooks around policy and result handling."""

    def __init__(
        self,
        *,
        policy_context: PolicyContext | None,
        audit: AuditWriter,
        graph: SessionGraph,
    ) -> None:
        self._policy_context = policy_context
        self._audit = audit
        self._graph = graph

    async def maybe_shadow_rewrite(
        self,
        session_id: Any,
        session: Any,
        tool_name: str,
        action: Any,
        proposed: PolicyDecision,
    ) -> PolicyDecision:
        """Pattern 6 shadow-mode rewrite for rule-driven non-ALLOW outcomes."""
        mode = getattr(session, "enforcement_mode", EnforcementMode.STRICT)
        if mode != EnforcementMode.SHADOW:
            return proposed
        if proposed.decision == Decision.ALLOW:
            return proposed
        if not proposed.rule or "no matching capability" in (proposed.reason or "").lower():
            return proposed

        await self._audit.write(
            Event(
                event_type=EventType.POLICY_SHADOWED,
                session_id=session_id,
                payload={
                    "tool": tool_name,
                    "would_be_decision": proposed.decision.value,
                    "rule": proposed.rule,
                    "reason": proposed.reason,
                    "target": getattr(action, "target", None),
                },
            ),
        )
        return dc_replace(
            proposed,
            decision=Decision.ALLOW,
            reason=(
                f"shadowed: would have been {proposed.decision.value} "
                f"under STRICT (rule={proposed.rule})"
            ),
        )

    async def apply_decision_inspectors(
        self,
        session_id: Any,
        session: Any,
        action: Any,
        tool_name: str,
        proposed: PolicyDecision,
    ) -> PolicyDecision:
        """Run DecisionInspectors and compose their relax/tighten outcomes."""
        if self._policy_context is None or not self._policy_context.decision_inspectors:
            return proposed

        inspect_action = SimpleNamespace(
            kind=action.kind,
            target=getattr(action, "target", ""),
            amount=getattr(action, "amount", None),
            relationship_group_ids=self._relationship_groups_for_action(action),
        )

        outcomes: list[tuple[str, Any]] = []
        for inspector in self._policy_context.decision_inspectors:
            try:
                outcome = inspector.inspect(
                    action=inspect_action,
                    session=session,
                    proposed_outcome=proposed,
                )
                if isawaitable(outcome):
                    outcome = await outcome
            except Exception as exc:
                await self._audit.write(
                    Event(
                        event_type=EventType.POLICY_DECIDED,
                        session_id=session_id,
                        payload={
                            "tool": tool_name,
                            "decision_inspector_error": str(exc),
                            "inspector": getattr(inspector, "name", "<unknown>"),
                        },
                    ),
                )
                continue
            if outcome is not None:
                outcomes.append((getattr(inspector, "name", "<unknown>"), outcome))

        composed = compose_inspector_outcomes(proposed.decision, outcomes)
        if composed is None:
            return proposed

        new_decision, rule_with_origin, rationale = composed
        if (
            is_strictly_less_restrictive(new_decision, proposed.decision)
            and proposed.decision != Decision.REQUIRE_APPROVAL
        ):
            await self._audit.write(
                Event(
                    event_type=EventType.RELAXATION_REFUSED,
                    session_id=session_id,
                    payload={
                        "tool": tool_name,
                        "refused_rule": rule_with_origin,
                        "base_decision": proposed.decision.value,
                        "attempted_decision": new_decision.value,
                        "reason": (
                            "inspector relax may not cross a structural floor "
                            "(only REQUIRE_APPROVAL is relaxable)"
                        ),
                    },
                ),
            )
            return proposed

        adjusted = dc_replace(
            proposed,
            decision=new_decision,
            rule=rule_with_origin,
            reason=rationale or proposed.reason,
        )
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

    async def apply_declassifiers(
        self,
        session_id: Any,
        session: Any,
        tool_name: str,
        value: Any,
        inherent_tags: LabelState,
        additional_tags: LabelState,
    ) -> tuple[Any, LabelState]:
        """Run the declassifier chain and return tags removed this turn."""
        if self._policy_context is None or not self._policy_context.declassifiers:
            return value, LabelState()

        try:
            final_value, applied = apply_declassifier_chain(
                tuple(self._policy_context.declassifiers),
                value=value,
                current_label_state=session.label_state,
                context={"tool": tool_name},
            )
        except Exception as exc:
            await self._audit.write(
                Event(
                    event_type=EventType.DECLASSIFIER_APPLIED,
                    session_id=session_id,
                    payload={"tool": tool_name, "error": str(exc)},
                ),
            )
            return value, LabelState()

        removed_categories: set[str] = set()
        removed_levels: set[ProvenanceLevel] = set()
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
            for entry in result.lower_axis_a_categories:
                category = entry.get("category", "")
                to_tier = entry.get("to_tier", "").lower()
                if to_tier == "none" and category:
                    removed_categories.add(category)
            self._collect_removed_provenance_levels(result.lower_axis_b_level, removed_levels)

        tags_to_remove = LabelState(
            a=frozenset(
                tag
                for tag in (inherent_tags.a | additional_tags.a)
                if tag.category in removed_categories
            ),
            b=frozenset(
                tag for tag in (inherent_tags.b | additional_tags.b) if tag.level in removed_levels
            ),
        )
        return final_value, tags_to_remove

    async def apply_inspectors(
        self,
        session: Any,
        value: object,
    ) -> None:
        """Run raise-only inspectors against a returned tool value."""
        if self._policy_context is None:
            return
        new_label_state = session.label_state
        for inspector in self._policy_context.inspectors:
            delta = inspector.inspect(
                value=value,
                current_label_state=new_label_state,
            )
            pre_state = new_label_state
            new_label_state = inherit(new_label_state, delta.raise_state)
            if new_label_state != pre_state:
                await self._audit.write(
                    Event(
                        event_type=EventType.INSPECTOR_APPLIED,
                        session_id=session.id,
                        payload={
                            "inspector": getattr(inspector, "__class__", type(inspector)).__name__,
                            "raised_axis_a": len(new_label_state.a) > len(pre_state.a),
                            "raised_axis_b": len(new_label_state.b) > len(pre_state.b),
                        },
                    ),
                )
        if new_label_state != session.label_state:
            updated = dc_replace(session, label_state=new_label_state)
            await self._graph._save(updated)
            self._graph._sessions[session.id] = updated

    def _relationship_groups_for_action(self, action: Any) -> tuple[str, ...]:
        if self._policy_context is None:
            return ()
        groups = getattr(self._policy_context, "relationship_groups", None)
        target = getattr(action, "target", None)
        if groups is None or not target:
            return ()
        try:
            return tuple(sorted(groups.resolve(str(target))))
        except Exception:
            return ()

    def _collect_removed_provenance_levels(
        self,
        lower_axis_b_level: str | None,
        removed_levels: set[ProvenanceLevel],
    ) -> None:
        if not lower_axis_b_level:
            return
        from capabledeputy.policy.labels import _PROVENANCE_RANK

        try:
            target_level = ProvenanceLevel(lower_axis_b_level)
            target_rank = _PROVENANCE_RANK[target_level]
        except (ValueError, KeyError):
            return
        for level in ProvenanceLevel:
            if _PROVENANCE_RANK[level] > target_rank:
                removed_levels.add(level)
