from typing import Any

import pytest

from capabledeputy.onguard import OnguardAdmissionError, OnguardRuntime, OnguardTask
from capabledeputy.onguard.clients import packaged_handlers


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


async def test_packaged_daily_digest_creates_review_artifact() -> None:
    daemon = FakeDaemon()
    daemon.responses["client.queue.claim"] = [
        {
            "command": {
                "command_id": "cmd-1",
                "client_id": "onguard.digest.daily",
                "command": "build_daily_digest",
                "payload": {"topics": ["security", "markets"]},
                "labels": ["personal.profile"],
            }
        }
    ]
    daemon.responses["artifact.create"] = [{"artifact": {"artifact_id": "artifact-digest-1"}}]
    runtime = OnguardRuntime(
        daemon,
        client_id="onguard.digest.daily",
        handlers=packaged_handlers(daemon),
    )

    assert await runtime.run_once() is True
    artifact_calls = [call for call in daemon.calls if call[0] == "artifact.create"]
    assert artifact_calls
    _, params = artifact_calls[0]
    assert params["artifact_type"] == "daily_digest"
    assert "review.required" in params["labels"]
    assert (
        "client.queue.complete",
        {
            "command_id": "cmd-1",
            "result": {
                "ok": True,
                "artifact_ref": "artifact-digest-1",
                "summary": "Prepared a digest draft from approved, labeled inputs.",
                "requires_human_review": True,
            },
            "artifact_ref": "artifact-digest-1",
        },
    ) in daemon.calls


async def test_packaged_finance_guard_quarantines_untrusted_documents() -> None:
    daemon = FakeDaemon()
    daemon.responses["client.registry.list"] = [
        {
            "clients": [
                {
                    "client_id": "onguard.finance.guard",
                    "kind": "onguard",
                    "status": "active",
                }
            ]
        }
    ]
    daemon.responses["client.queue.claim"] = [
        {
            "command": {
                "command_id": "cmd-1",
                "client_id": "onguard.finance.guard",
                "command": "guard_finance_document",
                "payload": {"source": "email", "amount": "100.00"},
                "labels": ["external-untrusted"],
            }
        }
    ]
    daemon.responses["artifact.create"] = [{"artifact": {"artifact_id": "artifact-finance-1"}}]
    runtime = OnguardRuntime(
        daemon,
        client_id="onguard.finance.guard",
        handlers=packaged_handlers(daemon),
    )

    assert await runtime.run_once() is True
    artifact_call = next(call for call in daemon.calls if call[0] == "artifact.create")
    assert artifact_call[1]["artifact_type"] == "finance_document_quarantine"
    assert artifact_call[1]["content"]["requires_human_override"] is True
    complete_call = next(call for call in daemon.calls if call[0] == "client.queue.complete")
    assert complete_call[1]["result"]["blocked"] is True


async def test_packaged_deterministic_approval_sweep_only_denies_matching_rules() -> None:
    daemon = FakeDaemon()
    daemon.responses["client.registry.list"] = [
        {
            "clients": [
                {
                    "client_id": "onguard.approval.deterministic",
                    "kind": "onguard",
                    "status": "active",
                }
            ]
        }
    ]
    daemon.responses["client.queue.claim"] = [
        {
            "command": {
                "command_id": "cmd-1",
                "client_id": "onguard.approval.deterministic",
                "command": "deterministic_approval_sweep",
                "payload": {
                    "deny_rules": [{"action_prefix": "delete", "target_contains": "/Downloads/"}]
                },
                "labels": [],
            }
        }
    ]
    daemon.responses["approval.list"] = [
        {
            "approvals": [
                {"id": 10, "action": "delete_file", "target": "/Downloads/tmp.txt"},
                {"id": 11, "action": "send_email", "target": "person@example.com"},
            ]
        }
    ]
    daemon.responses["approval.deny"] = [{"status": "denied"}]
    runtime = OnguardRuntime(
        daemon,
        client_id="onguard.approval.deterministic",
        handlers=packaged_handlers(daemon),
    )

    assert await runtime.run_once() is True
    deny_calls = [call for call in daemon.calls if call[0] == "approval.deny"]
    assert deny_calls == [
        (
            "approval.deny",
            {
                "id": 10,
                "decided_by": "onguard.approval.deterministic",
                "reason": (
                    "deterministic deny rule matched: "
                    "{'action_prefix': 'delete', 'target_contains': '/Downloads/'}"
                ),
            },
        )
    ]
