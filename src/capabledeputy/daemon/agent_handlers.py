"""RPC handlers for driving sessions through the agent loop."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from capabledeputy.agent.loop import run_turn
from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler
from capabledeputy.session.coordination import WorkstreamOwnershipError
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
    # Convert tags_added (LabelState) back to legacy label strings for
    # backward compatibility with clients.
    from capabledeputy.policy.labels import legacy_labels_present

    labels_added = legacy_labels_present(outcome.tags_added)

    return {
        "decision": outcome.decision.value,
        "rule": outcome.rule,
        "reason": outcome.reason,
        "error": outcome.error,
        "output": outcome.output,
        "tool_name": outcome.tool_name,
        "tool_args": outcome.tool_args,
        "approval_submission": outcome.approval_submission,
        "approval_id": outcome.approval_id,
        "labels_added": sorted(labels_added),
        # Issue #3 — Recovery steps surface to the chat REPL and to
        # the agent via policy.preview's output. Empty list when no
        # synthesis fired (ALLOW or unsupported rule).
        "recovery_steps": [_serialize_recovery_step(s) for s in (outcome.recovery_steps or ())],
    }


def make_agent_handlers(app: App) -> dict[str, Handler]:
    async def _run_single_turn(
        *,
        session_uuid: UUID,
        message: str,
        llm: Any,
        force_mode: Any = None,
        max_iterations: int | None = None,
        source: str = "client",
    ) -> dict[str, Any]:
        app.cancellation_flags[session_uuid] = False
        from capabledeputy.daemon.lifecycle import agent_max_iterations

        await app.session_coordinator.emit(
            session_uuid,
            "turn_started",
            {"source": source, "message": message},
        )
        try:
            result = await run_turn(
                session_id=session_uuid,
                user_message=message,
                llm=llm,
                tool_client=app.tool_client,
                registry=app.registry,
                graph=app.graph,
                audit=app.audit,
                max_iterations=max_iterations or agent_max_iterations(),
                force_mode=force_mode,
                model_pool=app.model_pool,
                cancel_check=lambda sid=session_uuid: app.cancellation_flags.get(
                    sid,
                    False,
                ),
            )
        finally:
            app.cancellation_flags.pop(session_uuid, None)
        response = {
            "content": result.content,
            "iterations": result.iterations,
            "finish_reason": result.finish_reason.value,
            "tool_outcomes": [_outcome_to_dict(o) for o in result.tool_outcomes],
        }
        await app.session_coordinator.emit(
            session_uuid,
            "turn_completed",
            {
                "source": source,
                "content": result.content,
                "iterations": result.iterations,
                "finish_reason": result.finish_reason.value,
                "tool_outcomes": response["tool_outcomes"],
            },
        )
        return response

    async def session_send(params: dict[str, Any]) -> dict[str, Any]:
        llm = app.llm_client
        if llm is None:
            raise RuntimeError(
                "no LLM client configured; daemon cannot drive the agent loop",
            )
        force_mode_str = params.get("mode")
        force_mode = None
        if force_mode_str:
            from capabledeputy.mode.dispatcher import ExecutionMode

            force_mode = ExecutionMode(force_mode_str)
        session_uuid = UUID(params["session_id"])
        message = str(params["message"])
        from capabledeputy.daemon.lifecycle import agent_max_iterations

        max_iterations = int(params.get("max_iterations") or agent_max_iterations())
        workstream = None
        try:
            workstream = await app.workstreams.ensure(
                session_uuid,
                str(params.get("client_id") or "interactive-client"),
                lease_seconds=int(params.get("lease_seconds") or 300),
                lease_token=params.get("lease_token"),
                reason=str(params.get("reason") or "interactive session activity"),
                auto_claim=bool(params.get("claim_if_missing", True)),
                admin_override=bool(params.get("admin_override", False)),
            )
        except WorkstreamOwnershipError as e:
            raise RuntimeError(str(e)) from e
        lock = await app.session_coordinator.acquire_turn(session_uuid)
        if lock is None:
            if params.get("enqueue_if_busy", True):
                item = app.session_coordinator.enqueue_input(
                    session_uuid,
                    message,
                    submitted_by=str(params.get("submitted_by", "client")),
                )
                await app.session_coordinator.emit(
                    session_uuid,
                    "input_queued",
                    {
                        "input": item.to_dict(),
                        "reason": "session_busy",
                        "pending_count": len(app.session_coordinator.pending_inputs(session_uuid)),
                    },
                )
                return {
                    "queued": True,
                    "reason": "session_busy",
                    "input": item.to_dict(),
                    "workstream": workstream.to_dict(include_token=False) if workstream else None,
                }
            raise RuntimeError(f"session {session_uuid} already has an active turn")

        queued_results: list[dict[str, Any]] = []
        try:
            first_result = await _run_single_turn(
                session_uuid=session_uuid,
                message=message,
                llm=llm,
                force_mode=force_mode,
                max_iterations=max_iterations,
                source=str(params.get("submitted_by", "client")),
            )
            while True:
                item = app.session_coordinator.pop_next_input(session_uuid)
                if item is None:
                    break
                queued_results.append(
                    await _run_single_turn(
                        session_uuid=session_uuid,
                        message=item.message,
                        llm=llm,
                        force_mode=force_mode,
                        max_iterations=max_iterations,
                        source=item.submitted_by,
                    ),
                )
        finally:
            app.session_coordinator.release_turn(session_uuid, lock)
        if queued_results:
            first_result["queued_results"] = queued_results
        if workstream is not None:
            first_result["workstream"] = workstream.to_dict(include_token=False)
        return first_result

    async def session_cancel(params: dict[str, Any]) -> dict[str, Any]:
        """Issue #23 — flip the cancellation flag for `session_id`'s
        active turn. The agent loop polls the flag at iteration
        boundaries and yields TurnInterrupted(reason="user_cancelled")
        on the next check. Idempotent: cancelling a session with no
        active turn returns `{cancelled: False}` rather than erroring,
        so the CLI's "send cancel on every Ctrl-C" pattern is safe.
        """
        session_uuid = UUID(params["session_id"])
        workstream = await app.workstreams.owner_for_session(session_uuid)
        if workstream is not None:
            actor = str(params.get("client_id") or "interactive-client")
            if workstream["client_id"] != actor and not bool(
                params.get("admin_override", False),
            ):
                raise RuntimeError(
                    f"workstream {workstream['id']} is owned by {workstream['client_id']}",
                )
        if session_uuid not in app.cancellation_flags:
            return {"cancelled": False, "reason": "no active turn"}
        app.cancellation_flags[session_uuid] = True
        return {"cancelled": True}

    async def session_turn_start(params: dict[str, Any]) -> dict[str, Any]:
        force_mode = None
        if params.get("mode"):
            from capabledeputy.mode.dispatcher import ExecutionMode

            force_mode = ExecutionMode(str(params["mode"]))
        return await app.turns.start(
            session_id=UUID(params["session_id"]),
            message=str(params["message"]),
            client_id=str(params.get("client_id") or "interactive-client"),
            force_mode=force_mode,
            max_iterations=(
                int(params["max_iterations"]) if params.get("max_iterations") is not None else None
            ),
            lease_token=params.get("lease_token"),
            lease_seconds=int(params.get("lease_seconds") or 300),
            heartbeat_interval_seconds=float(params.get("heartbeat_interval_seconds") or 5.0),
            heartbeat_timeout_seconds=float(params.get("heartbeat_timeout_seconds") or 30.0),
            heartbeat_enabled=bool(params.get("heartbeat_enabled", True)),
            admin_override=bool(params.get("admin_override", False)),
        )

    async def session_turn_get(params: dict[str, Any]) -> dict[str, Any]:
        return await app.turns.get(str(params["turn_id"]))

    async def session_turn_list(params: dict[str, Any]) -> dict[str, Any]:
        session_id = UUID(params["session_id"]) if params.get("session_id") else None
        return await app.turns.list(session_id=session_id)

    async def session_turn_events(params: dict[str, Any]) -> dict[str, Any]:
        return await app.turns.events_since(
            str(params["turn_id"]),
            cursor=int(params.get("cursor") or 0),
            limit=int(params.get("limit") or 200),
        )

    async def session_turn_ack(params: dict[str, Any]) -> dict[str, Any]:
        return await app.turns.ack(
            str(params["turn_id"]),
            client_id=(str(params["client_id"]) if params.get("client_id") else None),
        )

    async def session_turn_cancel(params: dict[str, Any]) -> dict[str, Any]:
        return await app.turns.cancel(
            str(params["turn_id"]),
            reason=str(params.get("reason") or "cancelled"),
        )

    async def session_grant_capability(params: dict[str, Any]) -> dict[str, Any]:
        from capabledeputy.policy.capabilities import Capability

        cap = Capability.from_dict(params["capability"])
        if cap.allows_destructive:
            raise ValueError(
                "allows_destructive capabilities cannot be granted via "
                "session.grant_capability — use operator.grant_capability "
                "after explicit operator consent, or approve the pending action",
            )
        session = await app.graph.grant_capability(UUID(params["session_id"]), cap)
        return session.to_dict()

    return {
        "session.send": session_send,
        "session.cancel": session_cancel,
        "session.turn.start": session_turn_start,
        "session.turn.get": session_turn_get,
        "session.turn.list": session_turn_list,
        "session.turn.events": session_turn_events,
        "session.turn.ack": session_turn_ack,
        "session.turn.cancel": session_turn_cancel,
        "session.grant_capability": session_grant_capability,
    }
