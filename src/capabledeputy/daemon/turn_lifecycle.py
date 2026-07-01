"""Daemon-owned streaming turn lifecycle.

This is the v0.33 bridge between the existing `run_turn_streaming()` generator
and clients that need cancellable, resumable, observable long-running turns.
The legacy `session.send` RPC remains available; new clients use
`session.turn.start` plus the `turn:<id>` subscription stream.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import anyio

from capabledeputy.agent.events import (
    LLMRequestSent,
    LLMTokenReceived,
    ToolDispatched,
    ToolReturned,
    TurnCompleted,
    TurnInterrupted,
    event_to_dict,
)
from capabledeputy.agent.loop import run_turn_streaming
from capabledeputy.daemon.image_attachments import image_attachment_payloads_from_outcome
from capabledeputy.audit.events import Event, EventType
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.labels import legacy_labels_present
from capabledeputy.session.coordination import WorkstreamOwnershipError

if TYPE_CHECKING:
    from capabledeputy.app import App


def _outcome_to_dict(outcome: Any) -> dict[str, Any]:
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
        "labels_added": sorted(legacy_labels_present(outcome.tags_added)),
        "recovery_steps": [
            {
                "command": getattr(step, "command", ""),
                "args": list(getattr(step, "args", ())),
                "rationale": getattr(step, "rationale", ""),
            }
            for step in (outcome.recovery_steps or ())
        ],
    }


@dataclass(frozen=True)
class TurnLifecycle:
    id: str
    session_id: UUID
    client_id: str
    message: str
    status: str
    created_at: datetime
    updated_at: datetime
    stream: str
    workstream_id: str | None = None
    heartbeat_interval_seconds: float = 5.0
    heartbeat_timeout_seconds: float = 30.0
    heartbeat_enabled: bool = True
    last_heartbeat_at: datetime | None = None
    cancel_reason: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    cursor: int = 0
    partial_content: str = ""
    partial_outcomes: tuple[dict[str, Any], ...] = ()

    def to_dict(self, *, include_message: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "session_id": str(self.session_id),
            "client_id": self.client_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "stream": self.stream,
            "workstream_id": self.workstream_id,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "heartbeat_timeout_seconds": self.heartbeat_timeout_seconds,
            "heartbeat_enabled": self.heartbeat_enabled,
            "last_heartbeat_at": (
                self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None
            ),
            "cancel_reason": self.cancel_reason,
            "result": self.result,
            "error": self.error,
            "cursor": self.cursor,
            "partial_content": self.partial_content,
            "partial_outcomes": list(self.partial_outcomes),
        }
        if include_message:
            data["message"] = self.message
        return data


class TurnLifecycleManager:
    def __init__(self, app: App, *, max_events_per_turn: int = 1000) -> None:
        self._app = app
        self._turns: dict[str, TurnLifecycle] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._cancel_scopes: dict[str, anyio.CancelScope] = {}
        self._cancel_reasons: dict[str, str] = {}
        self._active_by_session: dict[UUID, str] = {}
        self._lock = anyio.Lock()
        self._max_events_per_turn = max_events_per_turn
        self._emitted_image_paths: dict[str, set[str]] = {}
        self._tools_in_flight: dict[str, int] = {}

    async def start(
        self,
        *,
        session_id: UUID,
        message: str,
        client_id: str,
        force_mode: ExecutionMode | None = None,
        max_iterations: int | None = None,
        lease_token: str | None = None,
        lease_seconds: int = 300,
        heartbeat_interval_seconds: float = 5.0,
        heartbeat_timeout_seconds: float = 30.0,
        heartbeat_enabled: bool = True,
        admin_override: bool = False,
    ) -> dict[str, Any]:
        if self._app.llm_client is None:
            raise RuntimeError("no LLM client configured; daemon cannot drive the agent loop")
        from capabledeputy.daemon.lifecycle import agent_max_iterations

        workstream = None
        try:
            workstream = await self._app.workstreams.ensure(
                session_id,
                client_id,
                lease_seconds=lease_seconds,
                lease_token=lease_token,
                reason="streaming turn activity",
                auto_claim=True,
                admin_override=admin_override,
            )
        except WorkstreamOwnershipError as e:
            raise RuntimeError(str(e)) from e

        async with self._lock:
            active_id = self._active_by_session.get(session_id)
            if active_id is not None:
                active = self._turns.get(active_id)
                if active is not None and active.status == "running":
                    raise RuntimeError(f"session {session_id} already has active turn {active_id}")
            now = datetime.now(UTC)
            turn = TurnLifecycle(
                id=str(uuid4()),
                session_id=session_id,
                client_id=client_id,
                message=message,
                status="queued",
                created_at=now,
                updated_at=now,
                stream="",
                workstream_id=workstream.id,
                heartbeat_interval_seconds=max(0.1, float(heartbeat_interval_seconds)),
                heartbeat_timeout_seconds=max(0.1, float(heartbeat_timeout_seconds)),
                heartbeat_enabled=heartbeat_enabled,
                last_heartbeat_at=now,
            )
            turn = replace(turn, stream=f"turn:{turn.id}")
            self._turns[turn.id] = turn
            self._events[turn.id] = []
            self._active_by_session[session_id] = turn.id

        daemon = getattr(self._app, "daemon_server", None)
        if daemon is None or not hasattr(daemon, "start_background"):
            raise RuntimeError("daemon background task group is not available")
        daemon.start_background(
            self._run_turn,
            turn.id,
            force_mode,
            max_iterations or agent_max_iterations(),
        )
        await self._emit(turn.id, "turn_started", {"turn": turn.to_dict(include_message=True)})
        try:
            from capabledeputy.debug.chat_trace import log_turn_started

            session = self._app.graph.get(session_id)
            log_turn_started(
                turn_id=turn.id,
                session_id=str(session_id),
                client_id=client_id,
                message=message,
                purpose_handle=session.purpose_handle,
                stream=turn.stream,
            )
        except Exception:
            pass
        return {"turn": turn.to_dict(include_message=True)}

    async def cancel(self, turn_id: str, *, reason: str = "cancelled") -> dict[str, Any]:
        async with self._lock:
            turn = self._turns.get(turn_id)
            if turn is None:
                raise RuntimeError(f"turn {turn_id} not found")
            if turn.status not in {"queued", "running"}:
                return {"turn": turn.to_dict(include_message=True)}
            self._cancel_reasons[turn_id] = reason
            self._turns[turn_id] = replace(
                turn,
                status="cancelling",
                cancel_reason=reason,
                updated_at=datetime.now(UTC),
            )
            scope = self._cancel_scopes.get(turn_id)
        if scope is not None:
            scope.cancel()
        return {"turn": (await self.get(turn_id))["turn"]}

    async def ack(self, turn_id: str, *, client_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            turn = self._turns.get(turn_id)
            if turn is None:
                raise RuntimeError(f"turn {turn_id} not found")
            if client_id is not None and client_id != turn.client_id:
                raise RuntimeError(f"turn {turn_id} is owned by {turn.client_id}")
            updated = replace(
                turn,
                last_heartbeat_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self._turns[turn_id] = updated
            return {"turn": updated.to_dict(include_message=True)}

    async def get(self, turn_id: str) -> dict[str, Any]:
        async with self._lock:
            turn = self._turns.get(turn_id)
            if turn is None:
                raise RuntimeError(f"turn {turn_id} not found")
            return {"turn": turn.to_dict(include_message=True)}

    async def list(self, *, session_id: UUID | None = None) -> dict[str, Any]:
        async with self._lock:
            turns = list(self._turns.values())
        if session_id is not None:
            turns = [turn for turn in turns if turn.session_id == session_id]
        turns.sort(key=lambda turn: turn.created_at)
        return {"turns": [turn.to_dict(include_message=False) for turn in turns]}

    async def events_since(
        self,
        turn_id: str,
        *,
        cursor: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        async with self._lock:
            if turn_id not in self._turns:
                raise RuntimeError(f"turn {turn_id} not found")
            events = [event for event in self._events.get(turn_id, []) if event["cursor"] > cursor]
        if limit > 0:
            events = events[:limit]
        return {
            "events": events,
            "next_cursor": events[-1]["cursor"] if events else cursor,
        }

    def snapshot(self) -> dict[str, Any]:
        turns = list(self._turns.values())
        return {
            "count": len(turns),
            "active_count": sum(1 for turn in turns if turn.status in {"queued", "running"}),
            "items": [turn.to_dict(include_message=False) for turn in turns],
        }

    async def cancel_client_turns(self, client_id: str, *, reason: str) -> list[dict[str, Any]]:
        async with self._lock:
            turn_ids = [
                turn.id
                for turn in self._turns.values()
                if turn.client_id == client_id
                and turn.status in {"queued", "running", "cancelling"}
            ]
        out = []
        for turn_id in turn_ids:
            out.append((await self.cancel(turn_id, reason=reason))["turn"])
        return out

    async def _run_turn(
        self,
        turn_id: str,
        force_mode: ExecutionMode | None,
        max_iterations: int,
    ) -> None:
        lock: anyio.Lock | None = None
        turn = self._turns[turn_id]
        scope = anyio.CancelScope()
        trace_token = None
        try:
            from capabledeputy.debug.chat_trace import bind_turn

            trace_token = bind_turn(
                turn_id=turn_id,
                session_id=str(turn.session_id),
                client_id=turn.client_id,
            )
        except Exception:
            trace_token = None
        try:
            lock = await self._app.session_coordinator.acquire_turn(turn.session_id)
            if lock is None:
                raise RuntimeError(f"session {turn.session_id} already has an active turn")
            await self._set_status(turn_id, "running")
            self._app.cancellation_flags[turn.session_id] = False
            async with anyio.create_task_group() as tg:
                async with self._lock:
                    self._cancel_scopes[turn_id] = scope
                if turn.heartbeat_enabled:
                    tg.start_soon(self._heartbeat_watch, turn_id, scope)
                with scope:
                    async for event in run_turn_streaming(
                        session_id=turn.session_id,
                        user_message=turn.message,
                        llm=self._app.llm_client,
                        tool_client=self._app.tool_client,
                        registry=self._app.registry,
                        graph=self._app.graph,
                        audit=self._app.audit,
                        max_iterations=max_iterations,
                        force_mode=force_mode,
                        model_pool=self._app.model_pool,
                        cancel_check=lambda sid=turn.session_id: self._app.cancellation_flags.get(
                            sid,
                            False,
                        ),
                    ):
                        await self._record_agent_event(turn_id, event)
                        if isinstance(event, (TurnCompleted, TurnInterrupted)):
                            tg.cancel_scope.cancel()
                            break
                    if scope.cancel_called:
                        tg.cancel_scope.cancel()
        except BaseException as e:
            reason = self._cancel_reasons.get(turn_id)
            if reason is not None:
                await self._finish_interrupted(turn_id, reason)
            else:
                await self._finish_error(turn_id, e)
        finally:
            if trace_token is not None:
                try:
                    from capabledeputy.debug.chat_trace import unbind

                    unbind(trace_token)
                except Exception:
                    pass
            reason_to_finish: str | None = None
            session_id_for_cleanup = self._turns[turn_id].session_id
            async with self._lock:
                self._cancel_scopes.pop(turn_id, None)
                turn = self._turns.get(turn_id)
                if turn is not None and turn.status in {"queued", "running", "cancelling"}:
                    reason_to_finish = self._cancel_reasons.get(turn_id)
                current = self._turns.get(turn_id)
                if (
                    current is not None
                    and self._active_by_session.get(current.session_id) == turn_id
                ):
                    self._active_by_session.pop(current.session_id, None)
            if reason_to_finish is not None:
                await self._finish_interrupted(turn_id, reason_to_finish)
            self._app.cancellation_flags.pop(session_id_for_cleanup, None)
            if lock is not None:
                self._app.session_coordinator.release_turn(session_id_for_cleanup, lock)

    async def _heartbeat_watch(self, turn_id: str, scope: anyio.CancelScope) -> None:
        while True:
            await anyio.sleep(self._turns[turn_id].heartbeat_interval_seconds)
            should_cancel = False
            async with self._lock:
                turn = self._turns.get(turn_id)
                if turn is None or turn.status not in {"queued", "running"}:
                    return
                assert turn.last_heartbeat_at is not None
                if self._tools_in_flight.get(turn_id, 0) > 0:
                    now = datetime.now(UTC)
                    self._turns[turn_id] = replace(
                        turn,
                        last_heartbeat_at=now,
                        updated_at=now,
                    )
                    should_cancel = False
                else:
                    elapsed = (
                        datetime.now(UTC) - turn.last_heartbeat_at
                    ).total_seconds()
                    should_cancel = elapsed >= turn.heartbeat_timeout_seconds
                if should_cancel:
                    self._cancel_reasons[turn_id] = "heartbeat_timeout"
                    self._turns[turn_id] = replace(
                        turn,
                        status="cancelling",
                        cancel_reason="heartbeat_timeout",
                        updated_at=datetime.now(UTC),
                    )
            await self._emit(
                turn_id,
                "heartbeat",
                {"timeout_seconds": self._turns[turn_id].heartbeat_timeout_seconds},
            )
            if should_cancel:
                scope.cancel()
                return

    async def _touch_heartbeat(self, turn_id: str) -> None:
        """Refresh the heartbeat lease while the agent loop is making progress."""
        async with self._lock:
            turn = self._turns.get(turn_id)
            if turn is None or not turn.heartbeat_enabled:
                return
            self._turns[turn_id] = replace(
                turn,
                last_heartbeat_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

    async def _reset_partial_content(self, turn_id: str) -> None:
        async with self._lock:
            turn = self._turns.get(turn_id)
            if turn is None:
                return
            self._turns[turn_id] = replace(
                turn,
                partial_content="",
                updated_at=datetime.now(UTC),
            )

    async def _record_agent_event(self, turn_id: str, event: Any) -> None:
        if not isinstance(event, (TurnCompleted, TurnInterrupted)):
            await self._touch_heartbeat(turn_id)
        payload = event_to_dict(event)
        if isinstance(event, LLMRequestSent):
            await self._reset_partial_content(turn_id)
        elif isinstance(event, LLMTokenReceived):
            payload["partial_content"] = await self._append_partial_content(
                turn_id,
                event.text,
            )
            try:
                from capabledeputy.debug.chat_trace import log

                log(
                    "llm_token",
                    text=event.text,
                    partial_content=payload["partial_content"],
                    iteration=event.iteration,
                )
            except Exception:
                pass
        elif isinstance(event, ToolDispatched):
            self._tools_in_flight[turn_id] = self._tools_in_flight.get(turn_id, 0) + 1
        elif isinstance(event, ToolReturned):
            in_flight = max(0, self._tools_in_flight.get(turn_id, 0) - 1)
            if in_flight:
                self._tools_in_flight[turn_id] = in_flight
            else:
                self._tools_in_flight.pop(turn_id, None)
            payload["outcome"] = _outcome_to_dict(event.outcome)
            await self._merge_partial_outcome(turn_id, payload["outcome"])
            for attachment in image_attachment_payloads_from_outcome(payload["outcome"]):
                path = attachment["path"]
                seen = self._emitted_image_paths.setdefault(turn_id, set())
                if path in seen:
                    continue
                seen.add(path)
                await self._emit(turn_id, "image_attachment", attachment)
        elif isinstance(event, TurnCompleted):
            result = event.result
            payload["result"] = {
                "content": result.content,
                "iterations": result.iterations,
                "finish_reason": result.finish_reason.value,
                "tool_outcomes": [_outcome_to_dict(o) for o in result.tool_outcomes],
            }
            try:
                from capabledeputy.debug.chat_trace import log

                content = str(result.content or "")
                log(
                    "turn_completed",
                    iterations=result.iterations,
                    finish_reason=result.finish_reason.value,
                    content_len=len(content),
                    content_preview=content if len(content) <= 800 else content[:797] + "…",
                    n_tool_outcomes=len(result.tool_outcomes),
                )
            except Exception:
                pass
            await self._finish_completed(turn_id, payload["result"])
        elif isinstance(event, TurnInterrupted):
            payload["partial_outcomes"] = [_outcome_to_dict(o) for o in event.partial_outcomes]
            await self._finish_interrupted(
                turn_id,
                event.reason,
                partial_content=event.partial_content,
                partial_outcomes=tuple(payload["partial_outcomes"]),
            )
        await self._emit(turn_id, event.kind, payload)

    async def _append_partial_content(self, turn_id: str, text: str) -> str:
        async with self._lock:
            turn = self._turns[turn_id]
            updated = turn.partial_content + text
            self._turns[turn_id] = replace(
                turn,
                partial_content=updated,
                updated_at=datetime.now(UTC),
            )
            return updated

    async def _merge_partial_outcome(self, turn_id: str, outcome: dict[str, Any]) -> None:
        async with self._lock:
            turn = self._turns[turn_id]
            self._turns[turn_id] = replace(
                turn,
                partial_outcomes=(*turn.partial_outcomes, outcome),
                updated_at=datetime.now(UTC),
            )

    async def _set_status(self, turn_id: str, status: str) -> None:
        async with self._lock:
            turn = self._turns[turn_id]
            self._turns[turn_id] = replace(turn, status=status, updated_at=datetime.now(UTC))

    async def _finish_completed(self, turn_id: str, result: dict[str, Any]) -> None:
        self._emitted_image_paths.pop(turn_id, None)
        async with self._lock:
            turn = self._turns[turn_id]
            self._turns[turn_id] = replace(
                turn,
                status="completed",
                result=result,
                partial_content=str(result.get("content") or ""),
                updated_at=datetime.now(UTC),
            )

    async def _finish_interrupted(
        self,
        turn_id: str,
        reason: str,
        *,
        partial_content: str = "",
        partial_outcomes: tuple[dict[str, Any], ...] = (),
    ) -> None:
        async with self._lock:
            turn = self._turns[turn_id]
            if turn.status in {"completed", "interrupted", "error"}:
                return
            outcomes = partial_outcomes or turn.partial_outcomes
            merged_partial = partial_content or turn.partial_content
            self._turns[turn_id] = replace(
                turn,
                status="interrupted",
                cancel_reason=reason,
                partial_content=merged_partial,
                partial_outcomes=outcomes,
                updated_at=datetime.now(UTC),
            )
        await self._app.audit.write(
            Event(
                event_type=EventType.TURN_INTERRUPTED,
                session_id=self._turns[turn_id].session_id,
                payload={"turn_id": turn_id, "reason": reason},
            ),
        )
        await self._emit(
            turn_id,
            "interrupted",
            {
                "reason": reason,
                "partial_content": merged_partial,
                "partial_outcomes": list(outcomes),
            },
        )

    async def _finish_error(self, turn_id: str, exc: BaseException) -> None:
        async with self._lock:
            turn = self._turns[turn_id]
            self._turns[turn_id] = replace(
                turn,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                updated_at=datetime.now(UTC),
            )
        await self._emit(turn_id, "error", {"error_type": type(exc).__name__, "message": str(exc)})

    async def _emit(self, turn_id: str, event_type: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            await self._append_event_locked(turn_id, event_type, payload)

    async def _append_event_locked(
        self,
        turn_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        turn = self._turns[turn_id]
        cursor = turn.cursor + 1
        event = {
            "cursor": cursor,
            "turn_id": turn_id,
            "session_id": str(turn.session_id),
            "type": event_type,
            "payload": payload,
            "created_at": datetime.now(UTC).isoformat(),
        }
        events = self._events[turn_id]
        events.append(event)
        del events[:-self._max_events_per_turn]
        self._turns[turn_id] = replace(turn, cursor=cursor, updated_at=datetime.now(UTC))
        await self._app.session_coordinator.emit(
            turn.session_id,
            "turn_event",
            {"turn_id": turn_id, "event": event},
        )
        daemon = getattr(self._app, "daemon_server", None)
        if daemon is not None:
            await daemon.publish(turn.stream, event)
