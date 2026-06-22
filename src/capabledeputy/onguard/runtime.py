"""Reusable runtime loop for headless onguard clients.

The runtime is a client-side convenience layer only. It talks to the daemon
through RPC like every other client and never bypasses daemon policy,
approval, queue, schedule, event, or artifact handling.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


class OnguardDaemon(Protocol):
    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any: ...


@dataclass(frozen=True)
class OnguardTask:
    kind: str
    client_id: str
    command: str
    payload: dict[str, Any]
    labels: list[str]
    record: dict[str, Any]


OnguardHandler = Callable[[OnguardTask], dict[str, Any] | Awaitable[dict[str, Any]]]


class OnguardAdmissionError(RuntimeError):
    """Raised when the daemon has not admitted this onguard client."""


class OnguardRuntime:
    def __init__(
        self,
        daemon: OnguardDaemon,
        *,
        client_id: str,
        handlers: dict[str, OnguardHandler] | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> None:
        self.daemon = daemon
        self.client_id = client_id
        self.handlers = handlers or {}
        self.worker_id = worker_id or client_id
        self.lease_seconds = lease_seconds

    async def ensure_admitted(self) -> dict[str, Any]:
        result = await self.daemon.call("client.registry.list", {"kind": "onguard"})
        clients = result.get("clients", [])
        for client in clients:
            if client.get("client_id") == self.client_id and client.get("status") == "active":
                return client
        raise OnguardAdmissionError(f"onguard client is not admitted: {self.client_id}")

    async def run_once(self) -> bool:
        await self.ensure_admitted()
        if await self._run_due_schedule_once():
            return True
        return await self._run_queue_once()

    async def _run_due_schedule_once(self) -> bool:
        result = await self.daemon.call(
            "schedule.claim_due",
            {
                "client_id": self.client_id,
                "claimed_by": self.worker_id,
                "lease_seconds": self.lease_seconds,
            },
        )
        run = result.get("run")
        if run is None:
            return False
        task = OnguardTask(
            kind="schedule",
            client_id=self.client_id,
            command=f"schedule:{run['schedule_id']}",
            payload={},
            labels=[],
            record=run,
        )
        await self._dispatch_schedule(task)
        return True

    async def _run_queue_once(self) -> bool:
        result = await self.daemon.call(
            "client.queue.claim",
            {
                "client_id": self.client_id,
                "claimed_by": self.worker_id,
                "lease_seconds": self.lease_seconds,
            },
        )
        command = result.get("command")
        if command is None:
            return False
        task = OnguardTask(
            kind="command",
            client_id=self.client_id,
            command=str(command["command"]),
            payload=dict(command.get("payload") or {}),
            labels=[str(v) for v in command.get("labels", [])],
            record=command,
        )
        await self._dispatch_command(task)
        return True

    async def _dispatch_schedule(self, task: OnguardTask) -> None:
        handler = self.handlers.get(task.command) or self.handlers.get("schedule")
        try:
            result = await _call_handler(handler, task)
        except Exception as e:
            await self.daemon.call(
                "schedule.fail_run",
                {
                    "run_id": task.record["run_id"],
                    "result": {},
                    "error": str(e),
                },
            )
            await self._publish_event("schedule.failed", task, {"error": str(e)})
            return
        await self.daemon.call(
            "schedule.complete_run",
            {
                "run_id": task.record["run_id"],
                "result": result,
                "artifact_ref": result.get("artifact_ref"),
            },
        )
        await self._publish_event("schedule.completed", task, result)

    async def _dispatch_command(self, task: OnguardTask) -> None:
        handler = self.handlers.get(task.command)
        try:
            result = await _call_handler(handler, task)
        except Exception as e:
            await self.daemon.call(
                "client.queue.fail",
                {
                    "command_id": task.record["command_id"],
                    "result": {"error": str(e)},
                    "artifact_ref": None,
                },
            )
            await self._publish_event("command.failed", task, {"error": str(e)})
            return
        await self.daemon.call(
            "client.queue.complete",
            {
                "command_id": task.record["command_id"],
                "result": result,
                "artifact_ref": result.get("artifact_ref"),
            },
        )
        await self._publish_event("command.completed", task, result)

    async def _publish_event(
        self,
        event_type: str,
        task: OnguardTask,
        payload: dict[str, Any],
    ) -> None:
        await self.daemon.call(
            "client.events.publish",
            {
                "client_id": self.client_id,
                "command_id": task.record.get("command_id"),
                "schedule_id": task.record.get("schedule_id"),
                "event_type": event_type,
                "payload": payload,
                "labels": task.labels,
            },
        )


async def _call_handler(handler: OnguardHandler | None, task: OnguardTask) -> dict[str, Any]:
    if handler is None:
        raise RuntimeError(f"no onguard handler registered for {task.command}")
    result = handler(task)
    if inspect.isawaitable(result):
        result = await result
    return dict(result or {})
