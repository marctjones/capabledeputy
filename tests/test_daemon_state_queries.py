"""Live daemon read-model tests.

These cover the operator-facing state surfaces that a client can query
from the daemon: code identity, tool and session counts, approvals, and
the per-session security-context projection.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from capabledeputy.approval.model import ApprovalAction
from tests.daemon_integration import running_daemon


async def test_daemon_info_reports_code_and_state(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as daemon:
        session = await daemon.client.call("session.new", {"intent": "state probe"})
        await daemon.client.call(
            "approval.submit",
            {
                "from_session": session["id"],
                "action": ApprovalAction.SEND_EMAIL.value,
                "payload": "body",
                "target": "x@example.com",
                "justification": "probe",
            },
        )

        info = await daemon.client.call("daemon.info")

        assert info["version"]
        assert info["git_rev"]
        assert info["manifest_hash"]
        assert info["tool_count"] > 0
        assert info["session_count"] == 1
        assert info["session_count_active"] == 1
        assert info["upstream_servers"] == []
        assert info["audit_path"].endswith("audit.jsonl")


async def test_app_status_reports_model_and_pending_approvals(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as daemon:
        session = await daemon.client.call("session.new", {"intent": "status probe"})
        await daemon.client.call(
            "approval.submit",
            {
                "from_session": session["id"],
                "action": ApprovalAction.SEND_EMAIL.value,
                "payload": "body",
                "target": "x@example.com",
                "justification": "probe",
            },
        )

        status = await daemon.client.call("app.status")

        assert status["daemon"]["connected"] is True
        assert status["daemon"]["session_count"] == 1
        assert status["daemon"]["pending_approval_count"] == 1
        assert "model" in status
        assert "planner" in status["model"]
        assert "local_available" in status["model"]


async def test_session_security_context_materializes_approvals_and_models(
    tmp_path: Path,
) -> None:
    async with running_daemon(tmp_path) as daemon:
        session = await daemon.client.call("session.new", {"intent": "security-context probe"})
        await daemon.client.call(
            "approval.submit",
            {
                "from_session": session["id"],
                "action": ApprovalAction.SEND_EMAIL.value,
                "payload": "body",
                "target": "x@example.com",
                "justification": "probe",
            },
        )

        context = await daemon.client.call(
            "session.security_context",
            {"session_id": session["id"]},
        )

        assert context["schema_version"] == 1
        assert context["session"]["id"] == session["id"]
        assert context["approvals"]["pending_count"] == 1
        assert context["security_models"]
        assert any(m["name"] == "approval_declassification" for m in context["security_models"])
        assert any(p["name"] == "human_approval_gate" for p in context["flow_patterns"])
        assert context["actors"]["onguard"]["client"] is None


async def test_daemon_state_materializes_clients_sessions_workflows_and_memory(
    tmp_path: Path,
) -> None:
    async with running_daemon(tmp_path) as daemon:
        session = await daemon.client.call(
            "session.new",
            {"intent": "state snapshot", "owner": "operator"},
        )
        daemon.app.session_coordinator.enqueue_input(UUID(session["id"]), "hello")
        daemon.app.memory.write(
            "company.policy",
            {"name": "CapDep"},
            daemon.app.graph.get(UUID(session["id"])).label_state,
        )
        await daemon.client.call(
            "client.registry.register",
            {
                "client_id": "onguard.daily.digest",
                "kind": "onguard",
                "owner": "operator",
            },
        )
        await daemon.client.call(
            "client.queue.enqueue",
            {
                "client_id": "onguard.daily.digest",
                "command": "build_digest",
                "payload": {"scope": "daily"},
                "labels": ["personal.profile"],
                "provenance": {"source": "schedule"},
                "created_by": "operator",
            },
        )

        state = await daemon.client.call("daemon.state")

        assert state["schema_version"] == 1
        assert state["daemon"]["pid"]
        assert "connections" in state["daemon"]
        assert state["clients"]["session_clients"] == 1
        assert state["sessions"]["count"] == 1
        assert state["sessions"]["items"][0]["id"] == session["id"]
        assert state["sessions"]["items"][0]["coordinator"]["pending_input_count"] == 1
        assert state["workflows"]["interactive"]
        assert state["tools"]["count"] > 0
        assert state["memory"]["entry_count"] == 1
        assert state["memory"]["keys"] == ["company.policy"]
        assert state["labels"]["memory"]["company.policy"]["a"] == []
        assert state["labels"]["memory"]["company.policy"]["b"] == []
        assert state["coordination"]["pending_input_count"] == 1
        assert state["workstreams"]["count"] == 0
        assert any(
            client["client_id"] == "onguard.daily.digest" for client in state["onguard"]["clients"]
        )
        assert any(
            workflow["kind"] == "command" and workflow["client_id"] == "onguard.daily.digest"
            for workflow in state["workflows"]["onguard"]
        )
