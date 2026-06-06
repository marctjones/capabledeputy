"""RPC handlers for tool inspection, simulated dispatch, and real dispatch."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import kind_name
from capabledeputy.policy.engine import decide
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolDefinition, ToolRegistry


def _tool_to_dict(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "capability_kind": kind_name(tool.capability_kind),
        "target_arg": tool.target_arg,
        "amount_arg": tool.amount_arg,
        "parameters_schema": tool.parameters_schema,
    }


def make_tool_handlers(
    registry: ToolRegistry,
    graph: SessionGraph,
    tool_client: LabeledToolClient | None = None,
) -> dict[str, Handler]:
    async def tool_list(params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": [_tool_to_dict(t) for t in registry.list()]}

    async def tool_show(params: dict[str, Any]) -> dict[str, Any]:
        tool = registry.get(params["name"])
        return _tool_to_dict(tool)

    async def tool_test(params: dict[str, Any]) -> dict[str, Any]:
        tool = registry.get(params["tool"])
        session = graph.get(UUID(params["session_id"]))
        args = params.get("args", {})
        action = Action(
            kind=tool.capability_kind,
            target=tool.extract_target(args),
            amount=tool.extract_amount(args),
        )
        decision = decide(
            session.capability_set,
            action,
            used_kinds=session.used_kinds,
            cap_uses=session.cap_uses,
            labels=session.label_state,
        )
        return {
            "decision": decision.decision.value,
            "rule": decision.rule,
            "reason": decision.reason,
            "matched_capability": (
                decision.matched_capability.to_dict() if decision.matched_capability else None
            ),
            "tool": _tool_to_dict(tool),
            "action": {
                "kind": kind_name(action.kind),
                "target": action.target,
                "amount": action.amount,
            },
        }

    handlers: dict[str, Handler] = {
        "tool.list": tool_list,
        "tool.show": tool_show,
        "tool.test": tool_test,
    }

    if tool_client is not None:

        async def tool_call(params: dict[str, Any]) -> dict[str, Any]:
            from capabledeputy.policy.labels import _LEGACY_LABEL_STRINGS_TO_TAGS

            outcome = await tool_client.call_tool(
                session_id=UUID(params["session_id"]),
                tool_name=params["tool"],
                args=params.get("args", {}),
            )
            # Convert tags_added (LabelState) back to legacy label strings for
            # backward compatibility with MCP clients.
            labels_added = []
            for label_str, tags in _LEGACY_LABEL_STRINGS_TO_TAGS.items():
                # Egress labels map to empty LabelState, skip them
                if not tags.a and not tags.b:
                    continue
                # Check if this label's tags are all present in the added state
                if all(cat in outcome.tags_added.a for cat in tags.a) and all(
                    prov in outcome.tags_added.b for prov in tags.b
                ):
                    labels_added.append(label_str)
            return {
                "decision": outcome.decision.value,
                "output": outcome.output,
                "rule": outcome.rule,
                "reason": outcome.reason,
                "error": outcome.error,
                "labels_added": sorted(labels_added),
            }

        handlers["tool.call"] = tool_call

    return handlers
