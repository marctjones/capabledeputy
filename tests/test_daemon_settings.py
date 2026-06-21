from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.settings_handlers import make_settings_handlers
from capabledeputy.daemon.settings_store import (
    DaemonSettings,
    load_settings,
    update_settings,
)


def test_settings_store_defaults_and_updates(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"

    assert load_settings(path) == DaemonSettings()

    settings, changed = update_settings(
        {
            "show_thinking_output": True,
            "enable_screen_control": True,
        },
        path=path,
    )

    assert settings.show_thinking_output is True
    assert settings.enable_screen_control is True
    assert set(changed) == {"show_thinking_output", "enable_screen_control"}
    assert load_settings(path).show_thinking_output is True
    assert path.stat().st_mode & 0o777 == 0o600


def test_settings_store_rejects_unknown_and_wrong_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown setting"):
        update_settings({"not_real": True}, path=tmp_path / "settings.json")

    with pytest.raises(ValueError, match="must be boolean"):
        update_settings({"notifications_enabled": "yes"}, path=tmp_path / "settings.json")


async def test_settings_handlers_update_and_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    handlers = make_settings_handlers(app)

    result = await handlers["settings.update"](
        {"settings": {"notifications_enabled": False}},
    )

    assert result["settings"]["notifications_enabled"] is False
    assert result["changed"] == ["notifications_enabled"]
    events = await app.audit.read_all()
    assert events[-1].event_type.value == "setup.changed"
    assert events[-1].payload["action"] == "settings.update"


async def test_config_handlers_report_validation_and_log_locations(tmp_path: Path) -> None:
    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    config = tmp_path / "daemon.yaml"
    config.write_text("upstream_servers: []\n", encoding="utf-8")
    handlers = make_settings_handlers(app, config_path=config)

    validation = await handlers["config.validate"]({})
    logs = await handlers["config.log_locations"]({})

    assert validation["ok"] is True
    assert validation["config_path"] == str(config)
    assert logs["logs"][0]["path"] == str(tmp_path / "audit.jsonl")
