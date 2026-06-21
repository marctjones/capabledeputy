from __future__ import annotations

import stat
from pathlib import Path

import yaml

from capabledeputy.daemon.google_gmail_setup import (
    configure_gmail_oauth_client,
    configure_google_oauth_client,
    gmail_oauth_status,
    google_oauth_status,
    revoke_google_oauth_token,
)
from capabledeputy.upstream.server_yaml import ServerYamlConfig


def test_gmail_oauth_status_starts_unconfigured(tmp_path: Path) -> None:
    status = gmail_oauth_status(tmp_path)

    assert status["configured"] is False
    assert status["client_id_configured"] is False
    assert status["client_secret_configured"] is False
    assert status["token_configured"] is False


def test_configure_gmail_oauth_client_writes_secret_files_and_server_yaml(
    tmp_path: Path,
) -> None:
    status = configure_gmail_oauth_client(
        client_id="client-id",
        client_secret="client-secret",
        config_home=tmp_path,
    )

    assert status["configured"] is True
    assert status["client_id_configured"] is True
    assert status["client_secret_configured"] is True
    server_yaml = Path(status["server_yaml"])
    client_id_file = Path(status["client_id_file"])
    client_secret_file = Path(status["client_secret_file"])
    assert client_id_file.read_text(encoding="utf-8").strip() == "client-id"
    assert client_secret_file.read_text(encoding="utf-8").strip() == "client-secret"
    assert stat.S_IMODE(client_secret_file.stat().st_mode) == 0o600

    raw = yaml.safe_load(server_yaml.read_text(encoding="utf-8"))
    assert "client_secret" not in raw["auth"]
    assert raw["auth"]["client_id_file"] == str(client_id_file)
    assert raw["auth"]["client_secret_file"] == str(client_secret_file)

    parsed = ServerYamlConfig.from_dict(raw, filename=str(server_yaml))
    assert parsed.server_config.name == "google-gmail"
    assert parsed.server_config.auth is not None
    assert parsed.server_config.auth.client_id_file == str(client_id_file)
    assert parsed.server_config.disabled_kinds == frozenset({"SEND_EMAIL"})
    assert parsed.server_config.tool_overrides["create_draft"].target_arg == "to"


def test_configure_google_calendar_oauth_client_writes_managed_server_yaml(
    tmp_path: Path,
) -> None:
    status = configure_google_oauth_client(
        "google-calendar",
        client_id="calendar-client",
        client_secret="calendar-secret",
        config_home=tmp_path,
    )

    assert status["server"] == "google-calendar"
    assert status["display_name"] == "Google Calendar"
    assert status["configured"] is True
    raw = yaml.safe_load(Path(status["server_yaml"]).read_text(encoding="utf-8"))
    assert raw["url"] == "https://calendarmcp.googleapis.com/mcp/v1"
    assert raw["auth"]["scopes"] == ["https://www.googleapis.com/auth/calendar.readonly"]
    assert raw["strict"] is False


def test_revoke_google_oauth_token_removes_only_token_cache(tmp_path: Path) -> None:
    status = configure_google_oauth_client(
        "google-drive",
        client_id="drive-client",
        client_secret="drive-secret",
        config_home=tmp_path,
    )
    token_cache = Path(status["token_cache"])
    token_cache.write_text('{"access_token": "token"}', encoding="utf-8")

    revoked = revoke_google_oauth_token("google-drive", config_home=tmp_path)

    assert revoked["configured"] is True
    assert revoked["client_id_configured"] is True
    assert revoked["token_configured"] is False
    assert not token_cache.exists()


def test_unknown_google_oauth_service_is_rejected(tmp_path: Path) -> None:
    try:
        google_oauth_status("google-unknown", tmp_path)
    except ValueError as exc:
        assert "unknown Google OAuth service" in str(exc)
    else:
        raise AssertionError("unknown Google service should fail closed")
