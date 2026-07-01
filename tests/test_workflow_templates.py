from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.gui_handlers import make_gui_handlers
from capabledeputy.daemon.workflow_templates import (
    FIRST_WORKFLOW_TEMPLATE_ID,
    WorkflowConfigError,
    build_workflow_templates,
    validate_workflow_manifest,
    workflow_template_by_id,
    workflow_turn_message,
)


@pytest.fixture
def app(tmp_path: Path) -> App:
    return App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")


async def test_workflow_templates_rpc_returns_catalog(app: App) -> None:
    handlers = make_gui_handlers(app)
    result = await handlers["workflow.templates"]({})
    templates = result["templates"]

    assert len(templates) >= 6
    assert templates[0]["id"] == FIRST_WORKFLOW_TEMPLATE_ID
    assert workflow_template_by_id(FIRST_WORKFLOW_TEMPLATE_ID) is not None


def test_build_workflow_templates_matches_first_workflow_id() -> None:
    templates = build_workflow_templates()["templates"]
    ids = {template["id"] for template in templates}
    assert FIRST_WORKFLOW_TEMPLATE_ID in ids


def test_inbox_triage_template_includes_playbook_and_turn_message() -> None:
    template = workflow_template_by_id("inbox-triage")
    assert template is not None
    assert "Urgent" in template["agent_guidance"]
    assert "connector tools" in template["turn_message"]
    assert "mail.imap" not in template["turn_message"]
    assert template["turn_message"].startswith(template["prompt"])


def test_morning_briefing_template_includes_playbook_and_turn_message() -> None:
    template = workflow_template_by_id("morning-briefing")
    assert template is not None
    assert "Calendar" in template["agent_guidance"]
    assert "connector tools" in template["turn_message"]
    assert "google-gmail" not in template["turn_message"]
    assert template["turn_message"].startswith(template["prompt"])


def test_workflow_turn_message_omits_blank_guidance() -> None:
    message = workflow_turn_message({"prompt": "hello", "agent_guidance": ""})
    assert message == "hello"


def test_workflow_catalog_loads_from_configs_yaml() -> None:
    from capabledeputy.daemon.workflow_templates import _resolve_configs_dir

    path = _resolve_configs_dir() / "workflows.yaml"
    assert path.is_file()
    template = workflow_template_by_id("calendar-planning")
    assert template is not None
    assert template["requires_foreground_review"] is True


def test_workflow_templates_include_v036_manifest_schema() -> None:
    templates = build_workflow_templates()["templates"]

    for template in templates:
        assert template["schema_version"] == 1
        assert template["capabilities"]
        assert template["flow_pattern"]
        assert template["source_ports"]
        assert template["artifact_types"]
        assert template["approval_policy"]["mutating_actions"]
        assert template["retention"]["audit"] == "durable"


def test_workflow_manifest_validation_fails_closed_for_missing_schema_fields() -> None:
    with pytest.raises(WorkflowConfigError, match="missing schema fields"):
        validate_workflow_manifest(
            {
                "id": "legacy",
                "title": "Legacy",
                "prompt": "Do a thing.",
            },
            strict_schema=True,
        )


def test_workflow_manifest_validation_rejects_unknown_capability() -> None:
    with pytest.raises(WorkflowConfigError, match="unknown capability"):
        validate_workflow_manifest(
            {
                "id": "bad-kind",
                "title": "Bad",
                "prompt": "Do a thing.",
                "capabilities": ["BOGUS_KIND"],
                "flow_pattern": "background_read_review",
                "source_ports": ["gmail"],
                "artifact_types": ["research"],
                "approval_policy": {
                    "mutating_actions": "require_foreground_review",
                    "egress": "require_approval",
                    "foreground_review": "operator_visible",
                },
                "retention": {"source_context": "session", "artifacts": "session"},
            },
            strict_schema=True,
        )
