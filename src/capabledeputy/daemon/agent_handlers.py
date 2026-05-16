"""RPC handlers for driving sessions through the agent loop."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from capabledeputy.agent.loop import run_turn
from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler
from capabledeputy.tools.client import ToolCallOutcome


def _outcome_to_dict(outcome: ToolCallOutcome) -> dict[str, Any]:
    return {
        "decision": outcome.decision.value,
        "rule": outcome.rule,
        "reason": outcome.reason,
        "labels_added": sorted(label.value for label in outcome.labels_added),
        "error": outcome.error,
        "output": outcome.output,
        "tool_name": outcome.tool_name,
        "tool_args": outcome.tool_args,
        "approval_submission": outcome.approval_submission,
        "approval_id": outcome.approval_id,
    }


def make_agent_handlers(app: App) -> dict[str, Handler]:
    async def session_send(params: dict[str, Any]) -> dict[str, Any]:
        if app.llm_client is None:
            raise RuntimeError(
                "no LLM client configured; daemon cannot drive the agent loop",
            )
        force_mode_str = params.get("mode")
        force_mode = None
        if force_mode_str:
            from capabledeputy.mode.dispatcher import ExecutionMode

            force_mode = ExecutionMode(force_mode_str)
        result = await run_turn(
            session_id=UUID(params["session_id"]),
            user_message=str(params["message"]),
            llm=app.llm_client,
            tool_client=app.tool_client,
            registry=app.registry,
            graph=app.graph,
            audit=app.audit,
            max_iterations=int(params.get("max_iterations", 10)),
            force_mode=force_mode,
        )
        return {
            "content": result.content,
            "iterations": result.iterations,
            "finish_reason": result.finish_reason.value,
            "tool_outcomes": [_outcome_to_dict(o) for o in result.tool_outcomes],
        }

    async def session_grant_capability(params: dict[str, Any]) -> dict[str, Any]:
        from capabledeputy.policy.capabilities import Capability

        cap = Capability.from_dict(params["capability"])
        session = await app.graph.grant_capability(UUID(params["session_id"]), cap)
        return session.to_dict()

    return {
        "session.send": session_send,
        "session.grant_capability": session_grant_capability,
    }
