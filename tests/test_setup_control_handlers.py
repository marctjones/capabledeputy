from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.daemon.settings_store import update_settings
from capabledeputy.daemon.setup_control_handlers import make_setup_control_handlers


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> App:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")


async def test_runtime_controls_are_daemon_owned_and_audited(app: App) -> None:
    handlers = make_setup_control_handlers(app)

    paused = await handlers["runtime.automation_pause"]({"paused": True})
    screen = await handlers["runtime.screen_control.request"](
        {"session_id": "s1", "reason": "test"},
    )
    status = await handlers["runtime.status"]({})

    assert paused["runtime"]["automation_paused"] is True
    assert screen["runtime"]["screen_control_requested"] is True
    assert status["runtime"]["screen_control_session_id"] == "s1"
    assert screen["settings"]["enable_screen_control"] is True
    events = await app.audit.read_all()
    assert [event.payload["action"] for event in events[-2:]] == [
        "runtime.automation_paused",
        "runtime.screen_control.request",
    ]


async def test_setup_run_action_returns_safe_descriptors(app: App) -> None:
    handlers = make_setup_control_handlers(app)

    validate = await handlers["setup.run_action"]({"action_id": "config.validate"})
    macos = await handlers["setup.run_action"]({"action_id": "macos.automation_settings"})

    assert validate["method"] == "config.validate"
    assert validate["kind"] == "client_action"
    assert macos["kind"] == "open_url"
    assert macos["url"].startswith("x-apple.systempreferences:")


async def test_connector_status_reports_google_and_local_apps(app: App) -> None:
    handlers = make_setup_control_handlers(app)

    result = await handlers["connector.status"]({})

    ids = {connector["id"] for connector in result["connectors"]}
    assert {"google-gmail", "google-calendar", "google-drive", "apple-mail"} <= ids
    gmail = next(c for c in result["connectors"] if c["id"] == "google-gmail")
    assert gmail["status"] == "missing_credentials"
    assert gmail["actions"][0]["id"] == "setup.google_gmail.configure_oauth"


async def test_source_binding_upsert_preview_and_delete(app: App, tmp_path: Path) -> None:
    path = tmp_path / "source_bindings.yaml"
    path.write_text("bindings: []\n", encoding="utf-8")
    handlers = make_setup_control_handlers(app, source_bindings_path=path)

    created = await handlers["source_binding.upsert"](
        {
            "binding": {
                "name": "finance-folder",
                "scope_pattern_canonical": "file:///Users/marc/Documents/Finance/**",
                "category": "financial",
                "default_tier": "regulated",
                "write_discipline": "version-preserving",
                "risk_ids": ["FIN-001"],
            },
        },
    )
    listed = await handlers["source_binding.list"]({})
    preview = await handlers["source_binding.preview"](
        {"uri": "file:///Users/marc/Documents/Finance/bank.pdf"},
    )
    deleted = await handlers["source_binding.delete"]({"name": "finance-folder"})

    assert created["binding"]["name"] == "finance-folder"
    assert listed["bindings"][0]["category"] == "financial"
    assert preview["ok"] is True
    assert preview["tier"] == "regulated"
    assert preview["write_discipline"] == "version-preserving"
    assert deleted["deleted"] == "finance-folder"
    assert (await handlers["source_binding.list"]({}))["bindings"] == []


async def test_source_binding_rejects_broad_or_invalid_scope(app: App, tmp_path: Path) -> None:
    path = tmp_path / "source_bindings.yaml"
    path.write_text("bindings: []\n", encoding="utf-8")
    handlers = make_setup_control_handlers(app, source_bindings_path=path)

    with pytest.raises(ValueError, match="too broad"):
        await handlers["source_binding.upsert"](
            {
                "binding": {
                    "name": "too-broad",
                    "scope_pattern_canonical": "file:///*",
                    "category": "personal",
                    "default_tier": "sensitive",
                },
            },
        )

    preview = await handlers["source_binding.preview"]({"uri": "not-a-uri"})
    assert preview["ok"] is False


async def test_high_risk_approval_requires_strong_auth_when_touch_id_policy_enabled(
    app: App,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-auth"))
    update_settings({"require_touch_id_for_high_risk": True})
    session = await app.graph.new(intent="delete")
    approval = await app.approval_queue.submit(
        from_session=session.id,
        action=ApprovalAction.EXECUTE_DESTRUCTIVE,
        payload="delete /tmp/x",
        target="file:///tmp/x",
        justification="test",
    )
    handlers = make_approval_handlers(app)

    with pytest.raises(ValueError, match="strong authentication"):
        await handlers["approval.approve"]({"id": approval.id, "decided_by": "test"})

    result = await handlers["approval.approve"](
        {"id": approval.id, "decided_by": "test", "strong_auth": "touch_id"},
    )
    assert result["approval"]["status"] == "approved"
