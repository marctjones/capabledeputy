"""v0.34 smoke: setup readiness gate + morning briefing workflow under policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.daemon.setup_plan import FIRST_WORKFLOW_ID, build_setup_check
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.context import PolicyContext
from demos.scenarios._helpers import make_app, make_session


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