"""Source-label and reference-handle flow support for tool dispatch."""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import UUID, uuid4

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.patterns.reference_handle import (
    ReferenceHandleError,
    is_planner_safe_token,
)
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.labels import LabelState, most_restrictive_inherit
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.provenance import (
    ProvenanceRecorder,
    reference_bind_node_id,
    reference_handle_node_id,
)
from capabledeputy.tools.registry import ToolDefinition

RESTRICTED_SOURCE_FLOW_RULE = "restricted-source-requires-reference-or-sealed"


class ToolSourceFlow:
    """Pre-dispatch source taint and Pattern (3) handle binding.

    Tool dispatch needs to reason about data sources that are not visible in
    the caller's current session labels: memory keys, planner-safe reference
    handles, and similar indirect references. This collaborator keeps that
    extension point separate from policy decision orchestration.
    """

    def __init__(
        self,
        *,
        policy_context: PolicyContext | None,
        audit: AuditWriter,
    ) -> None:
        self._policy_context = policy_context
        self._audit = audit
        self._provenance = ProvenanceRecorder(audit)

    def extract_source_tags(
        self,
        *,
        session_id: UUID,
        tool: ToolDefinition,
        args: dict[str, Any],
    ) -> LabelState:
        """Return all labels known before policy decision and dispatch."""
        return most_restrictive_inherit(
            tool.extract_source_tags(args),
            self._reference_handle_arg_tags(
                session_id=session_id,
                tool=tool,
                args=args,
            ),
        )

    def restricted_source_floor_decision(
        self,
        *,
        tool: ToolDefinition,
        source_tags: LabelState,
        base_decision: PolicyDecision,
        labels_snapshot: LabelState,
        axis_d_snapshot: Any,
    ) -> PolicyDecision | None:
        """Refuse Pattern (2)-style declassification for restricted sources."""
        if not tool.forbid_restricted_source:
            return None
        if not any(tag.tier in {Tier.RESTRICTED, Tier.PROHIBITED} for tag in source_tags.a):
            return None
        return PolicyDecision(
            decision=Decision.DENY,
            rule=RESTRICTED_SOURCE_FLOW_RULE,
            reason=(
                "restricted/prohibited source data requires Pattern (3) "
                "reference handles or Pattern (5) sealed isolation; "
                f"{tool.name} cannot declassify it through Pattern (2)"
            ),
            matched_capability=base_decision.matched_capability,
            labels_snapshot=labels_snapshot,
            axis_d_snapshot=axis_d_snapshot,
            effect_class=tool.effect_class,
        )

    async def bind_reference_handles(
        self,
        *,
        session_id: UUID,
        tool: ToolDefinition,
        tool_name: str,
        args: dict[str, Any],
    ) -> tuple[dict[str, Any], LabelState, tuple[str, ...]]:
        """Substitute Pattern (3) handle ids with real values post-decision."""
        if (
            self._policy_context is None
            or self._policy_context.handle_store is None
            or not tool.accepts_handles
            or not tool.handle_arg_names
        ):
            return args, LabelState(), ()
        store = self._policy_context.handle_store
        substituted: dict[str, Any] = dict(args)
        bound_tags: list[LabelState] = []
        bound_node_ids: list[str] = []
        dest = self._destination_for_handle_bind(tool=tool, tool_name=tool_name, args=substituted)

        async def _bind_value(value: Any, path: str) -> Any:
            if isinstance(value, str) and is_planner_safe_token(value):
                try:
                    handle_id = UUID(value)
                except ValueError:
                    return value
                audit_id = uuid4()
                try:
                    bound = store.bind(
                        session_id=session_id,
                        handle_id=handle_id,
                        destination_canonical_id=dest,
                        tool=tool_name,
                        audit_id=audit_id,
                    )
                    bound_tags.append(store.label_state_for(session_id, handle_id))
                except ReferenceHandleError:
                    # Forged or cross-session handles are not substituted. The
                    # handler receives the original token and typically fails
                    # loudly without gaining access to hidden data.
                    return value
                event = Event(
                    audit_id=audit_id,
                    event_type=EventType.PATTERN3_HANDLE_BIND,
                    session_id=session_id,
                    payload={
                        "handle_id": str(handle_id),
                        "tool": tool_name,
                        "arg_name": path,
                        "destination_canonical_id": dest,
                    },
                )
                await self._audit.write(event)
                bind_node_id = reference_bind_node_id(audit_id)
                handle_node_id = reference_handle_node_id(handle_id)
                bound_node_ids.append(handle_node_id)
                await self._provenance.node(
                    session_id=session_id,
                    node_id=handle_node_id,
                    kind="reference_handle",
                    materialized_id=f"reference_handle:{handle_id}",
                )
                await self._provenance.node(
                    session_id=session_id,
                    node_id=bind_node_id,
                    kind="reference_bind",
                    materialized_id=f"reference_bind:{audit_id}",
                    event_audit_id=event.audit_id,
                    metadata={
                        "tool": tool_name,
                        "arg_name": path,
                        "destination_canonical_id": dest,
                    },
                )
                await self._provenance.edge(
                    session_id=session_id,
                    from_node_id=handle_node_id,
                    to_node_id=bind_node_id,
                    kind="bound",
                    event_audit_id=event.audit_id,
                )
                return bound
            if isinstance(value, dict):
                rebound: dict[Any, Any] = {}
                for key, child in value.items():
                    rebound[key] = await _bind_value(child, f"{path}.{key}")
                return rebound
            if isinstance(value, list):
                rebound_list: list[Any] = []
                for index, child in enumerate(value):
                    rebound_list.append(await _bind_value(child, f"{path}[{index}]"))
                return rebound_list
            return value

        for arg_name in tool.handle_arg_names:
            if arg_name in substituted:
                substituted[arg_name] = await _bind_value(substituted[arg_name], arg_name)

        return (
            substituted,
            most_restrictive_inherit(*bound_tags) if bound_tags else LabelState(),
            tuple(dict.fromkeys(bound_node_ids)),
        )

    def _destination_for_handle_bind(
        self,
        *,
        tool: ToolDefinition,
        tool_name: str,
        args: dict[str, Any],
    ) -> str:
        return (
            str(args.get(tool.target_arg, ""))
            if tool.surfaces_destination_id
            else f"tool:{tool_name}"
        ) or f"tool:{tool_name}"

    def _handle_tokens_in_value(self, value: Any, path: str) -> list[tuple[str, UUID]]:
        tokens: list[tuple[str, UUID]] = []
        if isinstance(value, str) and is_planner_safe_token(value):
            with contextlib.suppress(ValueError):
                tokens.append((path, UUID(value)))
            return tokens
        if isinstance(value, dict):
            for key, child in value.items():
                tokens.extend(self._handle_tokens_in_value(child, f"{path}.{key}"))
            return tokens
        if isinstance(value, list):
            for index, child in enumerate(value):
                tokens.extend(self._handle_tokens_in_value(child, f"{path}[{index}]"))
        return tokens

    def _reference_handle_arg_tags(
        self,
        *,
        session_id: UUID,
        tool: ToolDefinition,
        args: dict[str, Any],
    ) -> LabelState:
        if (
            self._policy_context is None
            or self._policy_context.handle_store is None
            or not tool.accepts_handles
            or not tool.handle_arg_names
        ):
            return LabelState()
        store = self._policy_context.handle_store
        tags: list[LabelState] = []
        for arg_name in tool.handle_arg_names:
            for _path, handle_id in self._handle_tokens_in_value(args.get(arg_name), arg_name):
                try:
                    tags.append(store.label_state_for(session_id, handle_id))
                except ReferenceHandleError:
                    continue
        return most_restrictive_inherit(*tags) if tags else LabelState()
