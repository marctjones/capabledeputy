"""RPC handlers for tool inspection, simulated dispatch, and real dispatch."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.actions import Action
from capabledeputy.policy.engine import decide
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolDefinition, ToolRegistry


def _tool_to_dict(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "capability_kind": tool.capability_kind.value,
        "target_arg": tool.target_arg,
        "amount_arg": tool.amount_arg,
        "inherent_labels": sorted(label.value for label in tool.inherent_labels),
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
            session.label_set,
            session.capability_set,
            action,
            used_kinds=session.used_kinds,
            cap_uses=session.cap_uses,
        )
        return {
            "decision": decision.decision.value,
            "rule": decision.rule,
            "reason": decision.reason,
            "matched_capability": (
                decision.matched_capability.to_dict() if decision.matched_capability else None
            ),
            "effective_labels": sorted(label.value for label in decision.effective_labels),
            "tool": _tool_to_dict(tool),
            "action": {
                "kind": action.kind.value,
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
            outcome = await tool_client.call_tool(
                session_id=UUID(params["session_id"]),
                tool_name=params["tool"],
                args=params.get("args", {}),
            )
            return {
                "decision": outcome.decision.value,
                "output": outcome.output,
                "rule": outcome.rule,
                "reason": outcome.reason,
                "labels_added": sorted(label.value for label in outcome.labels_added),
                "error": outcome.error,
            }

        handlers["tool.call"] = tool_call

    return handlers
