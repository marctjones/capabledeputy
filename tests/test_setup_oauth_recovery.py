"""v0.34 #143 — guided OAuth recovery descriptors for common connector states."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.google_gmail_setup import configure_google_oauth_client
from capabledeputy.daemon.setup_control_handlers import make_setup_control_handlers


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> App:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")


async def test_oauth_recovery_actions_for_missing_expired_and_connected_states(
    app: App,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers = make_setup_control_handlers(app)

    missing = await handlers["connector.status"]({})
    missing_gmail = next(c for c in missing["connectors"] if c["id"] == "google-gmail")
    assert missing_gmail["status"] == "missing_credentials"
    assert missing_gmail["actions"][0]["id"] == "setup.google.google-gmail.configure_oauth"

    configure_google_oauth_client(
        "google-gmail",
        client_id="gmail-id",
        client_secret="gmail-secret",
        config_home=tmp_path / "xdg",
    )
    reauth = await handlers["connector.status"]({})
    reauth_gmail = next(c for c in reauth["connectors"] if c["id"] == "google-gmail")
    assert reauth_gmail["status"] == "reauth_needed"
    login_action = next(
        action for action in reauth_gmail["actions"] if action["id"].endswith("oauth_login")
    )
    assert login_action["enabled"] is True
    login_descriptor = await handlers["setup.run_action"]({"action_id": login_action["id"]})
    assert login_descriptor["method"] == "setup.google.oauth_login"

    calendar = configure_google_oauth_client(
        "google-calendar",
        client_id="calendar-id",
        client_secret="calendar-secret",
        config_home=tmp_path / "xdg",
    )
    token_cache = Path(calendar["token_cache"])
    token_cache.parent.mkdir(parents=True, exist_ok=True)
    token_cache.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        app,
        "upstream_manager",
        SimpleNamespace(server_status={"calendar": SimpleNamespace(name="google-calendar")}),
        raising=False,
    )
    connected = await handlers["connector.status"]({})
    connected_calendar = next(c for c in connected["connectors"] if c["id"] == "google-calendar")
    assert connected_calendar["status"] == "connected"
    revoke_action = next(
        action for action in connected_calendar["actions"] if action["id"].endswith("oauth_revoke")
    )
    assert revoke_action["enabled"] is True