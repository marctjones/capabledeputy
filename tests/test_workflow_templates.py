from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.gui_handlers import make_gui_handlers
from capabledeputy.daemon.workflow_templates import (
    FIRST_WORKFLOW_TEMPLATE_ID,
    WorkflowConfigError,
    build_workflow_templates,
    first_workflow_template,
    first_workflow_template_id,
    validate_workflow_manifest,
    workflow_template_by_id,
    workflow_turn_message,
)
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from tests.daemon_integration import running_daemon


@pytest.fixture
def app(tmp_path: Path) -> App:
    return App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")


async def test_workflow_templates_rpc_returns_catalog(app: App) -> None:
    handlers = make_gui_handlers(app)
    result = await handlers["workflow.templates"]({})
    templates = result["templates"]

    assert len(templates) >= 6
    assert FIRST_WORKFLOW_TEMPLATE_ID is not None
    # The catalog is returned in configs/workflows.yaml declaration order,
    # not with the first workflow pinned to index 0 — just assert it's
    # present and independently resolvable.
    assert any(template["id"] == FIRST_WORKFLOW_TEMPLATE_ID for template in templates)
    assert workflow_template_by_id(FIRST_WORKFLOW_TEMPLATE_ID) is not None


def test_build_workflow_templates_matches_first_workflow_id() -> None:
    templates = build_workflow_templates()["templates"]
    ids = {template["id"] for template in templates}
    assert FIRST_WORKFLOW_TEMPLATE_ID in ids


def test_meeting_prep_template_includes_playbook_and_turn_message() -> None:
    template = workflow_template_by_id("meeting-prep")
    assert template is not None
    assert "Meeting prep playbook" in template["agent_guidance"]
    assert template["turn_message"].startswith(template["prompt"])
    assert template["agent_guidance"] in template["turn_message"]


def test_first_workflow_template_none_when_catalog_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The built-in catalog is empty since Google Workspace removal (no
    bundled workflows ship without configs/workflows.yaml). Both
    first_workflow_template() and first_workflow_template_id() must
    degrade to None rather than crash when the catalog is empty —
    this is a real behavior change worth pinning."""
    import capabledeputy.daemon.workflow_templates as workflow_templates_module

    monkeypatch.setattr(workflow_templates_module, "_workflow_catalog", lambda: (None, ()))

    assert first_workflow_template_id() is None
    assert first_workflow_template() is None


def test_workflow_turn_message_omits_blank_guidance() -> None:
    message = workflow_turn_message({"prompt": "hello", "agent_guidance": ""})
    assert message == "hello"


async def test_workflow_launch_starts_daemon_owned_turn(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as running:
        running.app.llm_client = FakeLLMClient(
            [LLMResponse(content="briefing ready", finish_reason=FinishReason.STOP)],
        )

        launched = await running.client.call(
            "workflow.launch",
            {
                "template_id": "summarize-selection",
                "client_id": "test-workflow",
                "heartbeat_enabled": False,
            },
        )

        assert launched["template"]["id"] == "summarize-selection"
        assert launched["session"]["purpose_handle"] == "general"
        assert launched["turn"]["client_id"] == "test-workflow"
        assert launched["turn"]["status"] in {"queued", "running", "completed"}


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


def test_workflow_templates_include_v036_starter_set() -> None:
    templates = {template["id"]: template for template in build_workflow_templates()["templates"]}

    assert {
        "calendar-planning",
        "meeting-prep",
        "research-memo",
        "web-research",
        "summarize-selection",
        "revise-document",
    } <= set(templates)
    assert templates["meeting-prep"]["flow_pattern"] == "foreground_context_review"
    assert templates["research-memo"]["artifact_types"] == ["chart", "image", "research"]


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
