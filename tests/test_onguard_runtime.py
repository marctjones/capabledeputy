from typing import Any

import pytest

from capabledeputy.onguard import OnguardAdmissionError, OnguardRuntime, OnguardTask


class FakeDaemon:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, list[dict[str, Any]]] = {
            "client.registry.list": [
                {
                    "clients": [
                        {
                            "client_id": "onguard.digest.daily",
                            "kind": "onguard",
                            "status": "active",
                        }
                    ]
                }
            ],
            "schedule.claim_due": [{"run": None}],
            "client.queue.claim": [{"command": None}],
        }

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params or {}))
        queue = self.responses.setdefault(method, [{}])
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]


async def test_runtime_refuses_without_daemon_admission() -> None:
    daemon = FakeDaemon()
    daemon.responses["client.registry.list"] = [{"clients": []}]
    runtime = OnguardRuntime(daemon, client_id="onguard.digest.daily")

    with pytest.raises(OnguardAdmissionError):
        await runtime.run_once()


async def test_runtime_claims_and_completes_queue_commands() -> None:
    daemon = FakeDaemon()
    daemon.responses["client.queue.claim"] = [
        {
            "command": {
                "command_id": "cmd-1",
                "client_id": "onguard.digest.daily",
                "command": "build_digest",
                "payload": {"date": "today"},
                "labels": ["personal.profile"],
            }
        }
    ]

    async def build_digest(task: OnguardTask) -> dict[str, Any]:
        assert task.payload == {"date": "today"}
        return {"ok": True, "artifact_ref": "artifact-1"}

    runtime = OnguardRuntime(
        daemon,
        client_id="onguard.digest.daily",
        handlers={"build_digest": build_digest},
    )

    assert await runtime.run_once() is True
    assert (
        "client.queue.complete",
        {
            "command_id": "cmd-1",
            "result": {"ok": True, "artifact_ref": "artifact-1"},
            "artifact_ref": "artifact-1",
        },
    ) in daemon.calls
    assert any(method == "client.events.publish" for method, _ in daemon.calls)


async def test_runtime_reports_handler_failures_through_daemon() -> None:
    daemon = FakeDaemon()
    daemon.responses["client.queue.claim"] = [
        {
            "command": {
                "command_id": "cmd-1",
                "client_id": "onguard.digest.daily",
                "command": "build_digest",
                "payload": {},
                "labels": [],
            }
        }
    ]

    def fail(_task: OnguardTask) -> dict[str, Any]:
        raise RuntimeError("boom")

    runtime = OnguardRuntime(
        daemon,
        client_id="onguard.digest.daily",
        handlers={"build_digest": fail},
    )

    assert await runtime.run_once() is True
    assert (
        "client.queue.fail",
        {
            "command_id": "cmd-1",
            "result": {"error": "boom"},
            "artifact_ref": None,
        },
    ) in daemon.calls


async def test_runtime_claims_and_completes_due_schedule() -> None:
    daemon = FakeDaemon()
    daemon.responses["schedule.claim_due"] = [
        {
            "run": {
                "run_id": "run-1",
                "schedule_id": "daily-news",
                "client_id": "onguard.digest.daily",
                "status": "claimed",
            }
        }
    ]

    runtime = OnguardRuntime(
        daemon,
        client_id="onguard.digest.daily",
        handlers={"schedule:daily-news": lambda _task: {"ok": True}},
    )

    assert await runtime.run_once() is True
    assert (
        "schedule.complete_run",
        {
            "run_id": "run-1",
            "result": {"ok": True},
            "artifact_ref": None,
        },
    ) in daemon.calls
