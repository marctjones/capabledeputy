"""v0.34 smoke: setup readiness gate + morning briefing workflow under policy."""

from __future__ import annotations

import os
import platform
from pathlib import Path

import anyio
import pytest
from demos.scenarios._helpers import make_app, make_session

from capabledeputy.daemon.setup_plan import FIRST_WORKFLOW_ID, build_setup_check
from capabledeputy.daemon.workflow_templates import (
    first_workflow_template,
    workflow_turn_message,
)
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.labels import LabelState
from tests.daemon_integration import running_daemon


@pytest.fixture
def app(tmp_path: Path):
    return make_app(tmp_path)


async def test_setup_to_morning_briefing_smoke(app) -> None:
    app.policy_context = PolicyContext()
    await app.startup()
    app.llm_client = FakeLLMClient([])
    app.quarantined_llm = app.llm_client

    check = build_setup_check(app)
    assert check["workflow_ready"] is True
    assert check["first_workflow"] == FIRST_WORKFLOW_ID
    assert check["blocking_steps"] == []

    caps = frozenset(
        {
            Capability(kind=CapabilityKind.READ_FS, pattern="*"),
            Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*"),
        },
    )
    session = await make_session(app, capabilities=caps, purpose_handle="general")
    out = await app.tool_client.call_tool(session.id, "memory.read", {"key": "todo"})
    assert out.decision.value == "allow"


async def _wait_for_turn(
    client,
    turn_id: str,
    *,
    timeout: float = 8.0,
) -> dict:
    deadline = anyio.current_time() + timeout
    last: dict = {}
    while anyio.current_time() < deadline:
        last = await client.call("session.turn.get", {"turn_id": turn_id})
        status = str((last.get("turn") or {}).get("status") or "")
        if status in {"completed", "interrupted", "error"}:
            return last
        await anyio.sleep(0.05)
    raise AssertionError(f"turn {turn_id} did not complete; last={last}")


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="live daily-driver briefing E2E depends on macOS-resolved workflow tooling (#354)",
)
async def test_morning_briefing_live_daemon_e2e(tmp_path: Path) -> None:
    """Setup plan → foreground session → safe reads, blocked egress, workflow turn."""
    notes_key = f"{os.path.expanduser('~')}/notes/todo"
    briefing_llm = FakeLLMClient(
        [
            LLMResponse(
                content="Reading notes for the briefing.",
                tool_calls=(ToolCall(id="mb1", name="memory.read", args={"key": notes_key}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content=("Morning briefing:\n- Call dentist today\n- Buy milk on the way home"),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )

    async with running_daemon(tmp_path) as running:
        running.app.llm_client = briefing_llm
        running.app.quarantined_llm = briefing_llm
        running.app.memory.write(
            notes_key,
            "call dentist; buy milk",
            LabelState(),
        )

        plan = await running.client.call("setup.plan", {})
        check = await running.client.call("setup.check", {})
        workflows = await running.client.call("workflow.templates", {})

        assert plan["first_workflow"]["id"] == FIRST_WORKFLOW_ID
        assert plan["workflow_ready"] is True
        assert check["workflow_ready"] is True
        assert check["first_workflow"] == FIRST_WORKFLOW_ID

        template = first_workflow_template()
        listed = next(item for item in workflows["templates"] if item["id"] == FIRST_WORKFLOW_ID)
        assert listed["purpose_handle"] == template["purpose_handle"]
        assert listed["turn_message"] == workflow_turn_message(template)

        session = await running.client.call(
            "session.new",
            {
                "intent": template["title"],
                "owner": "CapDepMac",
                "purpose_handle": template["purpose_handle"],
            },
        )
        session_id = str(session["id"])
        cap_kinds = {cap["kind"] for cap in session.get("capability_set", [])}
        assert CapabilityKind.GMAIL_READ.value in cap_kinds
        assert CapabilityKind.CALENDAR_READ.value in cap_kinds
        assert CapabilityKind.SEND_EMAIL.value not in cap_kinds

        read_outcome = await running.client.call(
            "tool.call",
            {
                "session_id": session_id,
                "tool": "memory.read",
                "args": {"key": notes_key},
            },
        )
        assert read_outcome["decision"] == "allow"

        send_outcome = await running.client.call(
            "tool.call",
            {
                "session_id": session_id,
                "tool": "email.send",
                "args": {
                    "to": "me@example.com",
                    "subject": "briefing",
                    "body": "exfil attempt",
                },
            },
        )
        assert send_outcome["decision"] == "deny"

        started = await running.client.call(
            "session.turn.start",
            {
                "session_id": session_id,
                "message": listed["turn_message"],
                "client_id": "test-morning-briefing",
                "heartbeat_enabled": False,
            },
        )
        turn_id = str(started["turn"]["id"])
        finished = await _wait_for_turn(running.client, turn_id)
        assert finished["turn"]["status"] == "completed"
        content = str((finished["turn"].get("result") or {}).get("content") or "")
        assert "dentist" in content.lower()
