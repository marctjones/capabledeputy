from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.gui_handlers import make_gui_handlers
from capabledeputy.policy.context import PolicyContext
from capabledeputy.daemon.setup_plan import (
    FIRST_WORKFLOW_ID,
    build_setup_check,
    build_setup_plan,
)


@pytest.fixture
def app(tmp_path: Path) -> App:
    return App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")


async def test_setup_plan_reports_ordered_steps_and_workflow(app: App) -> None:
    handlers = make_gui_handlers(app)

    plan = await handlers["setup.plan"]({})
    check = await handlers["setup.check"]({})

    assert plan["first_workflow"]["id"] == FIRST_WORKFLOW_ID
    assert plan["steps"]
    assert plan["steps"][0]["id"] == "daemon"
    assert plan["summary"]["ok"] >= 1
    assert "checks" in plan
    assert check["first_workflow"] == FIRST_WORKFLOW_ID
    assert check["ready"] == plan["ready"]
    assert check["workflow_ready"] == plan["workflow_ready"]


async def test_setup_status_matches_plan_checks(app: App) -> None:
    handlers = make_gui_handlers(app)

    status = await handlers["setup.status"]({})
    plan = await handlers["setup.plan"]({})

    assert status["checks"] == plan["checks"]


async def test_setup_check_flags_missing_model_as_blocking(app: App) -> None:
    app.llm_client = None

    check = build_setup_check(app)
    plan = build_setup_plan(app)

    assert check["workflow_ready"] is False
    assert "model" in plan["first_workflow"]["blockers"]
    assert any(step["id"] == "model" and step["blocking"] for step in plan["steps"])


async def test_setup_plan_workflow_ready_with_model_and_policy(app: App) -> None:
    from capabledeputy.llm.fake import FakeLLMClient

    app.llm_client = FakeLLMClient([])
    app.policy_context = PolicyContext()

    plan = build_setup_plan(app)

    assert plan["workflow_ready"] is True
    assert plan["first_workflow"]["ready"] is True
    assert plan["first_workflow"]["blockers"] == []


async def test_setup_plan_includes_imap_email_step(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.policy_context = PolicyContext()
    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.imap_credentials_present",
        lambda: False,
    )

    plan = build_setup_plan(app)
    imap_step = next(step for step in plan["steps"] if step["id"] == "imap-email")

    assert imap_step["status"] == "warning"
    assert "imap-setup" in imap_step["detail"]