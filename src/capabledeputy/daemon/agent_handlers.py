"""RPC handlers for driving sessions through the agent loop."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from capabledeputy.agent.loop import run_turn
from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler
from capabledeputy.tools.client import ToolCallOutcome


def _serialize_recovery_step(step: Any) -> dict[str, Any]:
    """RecoveryStep → dict for the wire. Tolerant: accepts either a
    real RecoveryStep dataclass or a dict (passed through unchanged)."""
    if isinstance(step, dict):
        return step
    return {
        "command": getattr(step, "command", ""),
        "args": list(getattr(step, "args", ())),
        "rationale": getattr(step, "rationale", ""),
    }


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
        # Issue #3 — Recovery steps surface to the chat REPL and to
        # the agent via policy.preview's output. Empty list when no
        # synthesis fired (ALLOW or unsupported rule).
        "recovery_steps": [_serialize_recovery_step(s) for s in (outcome.recovery_steps or ())],
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
        session_uuid = UUID(params["session_id"])
        # Issue #23 — register the cancellation flag so session.cancel
        # (issued from a different RPC connection) can flip it. Clear
        # in `finally` so the entry doesn't outlive the turn — a stale
        # flag would otherwise cancel the *next* turn before it
        # started.
        app.cancellation_flags[session_uuid] = False
        try:
            result = await run_turn(
                session_id=session_uuid,
                user_message=str(params["message"]),
                llm=app.llm_client,
                tool_client=app.tool_client,
                registry=app.registry,
                graph=app.graph,
                audit=app.audit,
                max_iterations=int(params.get("max_iterations", 50)),
                force_mode=force_mode,
                cancel_check=lambda sid=session_uuid: app.cancellation_flags.get(
                    sid, False,
                ),
            )
        finally:
            app.cancellation_flags.pop(session_uuid, None)
        return {
            "content": result.content,
            "iterations": result.iterations,
            "finish_reason": result.finish_reason.value,
            "tool_outcomes": [_outcome_to_dict(o) for o in result.tool_outcomes],
        }

    async def session_cancel(params: dict[str, Any]) -> dict[str, Any]:
        """Issue #23 — flip the cancellation flag for `session_id`'s
        active turn. The agent loop polls the flag at iteration
        boundaries and yields TurnInterrupted(reason="user_cancelled")
        on the next check. Idempotent: cancelling a session with no
        active turn returns `{cancelled: False}` rather than erroring,
        so the CLI's "send cancel on every Ctrl-C" pattern is safe.
        """
        session_uuid = UUID(params["session_id"])
        if session_uuid not in app.cancellation_flags:
            return {"cancelled": False, "reason": "no active turn"}
        app.cancellation_flags[session_uuid] = True
        return {"cancelled": True}

    async def session_grant_capability(params: dict[str, Any]) -> dict[str, Any]:
        from capabledeputy.policy.capabilities import Capability

        cap = Capability.from_dict(params["capability"])
        session = await app.graph.grant_capability(UUID(params["session_id"]), cap)
        return session.to_dict()

    return {
        "session.send": session_send,
        "session.cancel": session_cancel,
        "session.grant_capability": session_grant_capability,
    }
