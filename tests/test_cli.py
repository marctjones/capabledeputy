from pathlib import Path

import pytest
from typer.testing import CliRunner

from capabledeputy.cli.main import app
from capabledeputy.version import __version__
from tests._socket_helpers import short_socket_path

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_socket_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_socket_path().parent))


def test_version_command_when_daemon_not_running() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
    assert "daemon not running" in result.stdout


def test_daemon_status_exits_nonzero_when_not_running() -> None:
    result = runner.invoke(app, ["daemon", "status"])
    assert result.exit_code == 1
    assert "not running" in result.stdout


def test_daemon_stop_exits_nonzero_when_not_running() -> None:
    result = runner.invoke(app, ["daemon", "stop"])
    assert result.exit_code == 1


def test_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "capable deputy" in result.stdout.lower()


def test_onguard_builtins_lists_packaged_clients() -> None:
    result = runner.invoke(app, ["onguard", "builtins"])
    assert result.exit_code == 0
    assert "onguard.digest.daily" in result.stdout
    assert "onguard.finance.guard" in result.stdout


def test_daemon_help_runs() -> None:
    result = runner.invoke(app, ["daemon", "--help"])
    assert result.exit_code == 0
    assert "daemon" in result.stdout.lower()


def test_policy_models_surfaces_biba_gap() -> None:
    # Issue #53 — `capdep policy models` must loudly surface that Biba is
    # one-direction only, so no operator assumes full Biba. Static
    # command; needs no running daemon.
    result = runner.invoke(app, ["policy", "models"])
    assert result.exit_code == 0
    out = result.output
    assert "Biba" in out
    assert "GAP" in out


def test_google_oauth_cli_status_dispatches_to_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, str | None]] = []

    async def fake_call(rpc: str, params: dict, *, socket_path: str | None) -> dict:
        calls.append((rpc, params, socket_path))
        return {
            "service_id": "google-calendar",
            "display_name": "Google Calendar",
            "configured": True,
            "client_id_configured": True,
            "client_secret_configured": True,
            "token_configured": False,
            "server_yaml": "/tmp/google-calendar.yaml",
        }

    monkeypatch.setattr("capabledeputy.cli.main._onguard_call", fake_call)

    result = runner.invoke(app, ["oauth", "google", "status", "google-calendar"])

    assert result.exit_code == 0
    assert calls == [
        ("setup.google.oauth_status", {"service_id": "google-calendar"}, None),
    ]
    assert "Google Calendar" in result.stdout
    assert "token=no" in result.stdout


def test_google_oauth_cli_configure_dispatches_to_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict, str | None]] = []

    async def fake_call(rpc: str, params: dict, *, socket_path: str | None) -> dict:
        calls.append((rpc, params, socket_path))
        return {
            "service_id": params["service_id"],
            "display_name": "Google Gmail",
            "configured": True,
            "client_id_configured": True,
            "client_secret_configured": True,
            "token_configured": False,
        }

    monkeypatch.setattr("capabledeputy.cli.main._onguard_call", fake_call)

    result = runner.invoke(
        app,
        [
            "oauth",
            "google",
            "configure",
            "google-gmail",
            "--client-id",
            "cid",
            "--client-secret",
            "secret",
            "--socket",
            "/tmp/capdep.sock",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "setup.google.configure_oauth",
            {"service_id": "google-gmail", "client_id": "cid", "client_secret": "secret"},
            "/tmp/capdep.sock",
        ),
    ]
    assert "Google Gmail" in result.stdout


def test_google_oauth_cli_login_can_disable_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, str | None]] = []

    async def fake_call(rpc: str, params: dict, *, socket_path: str | None) -> dict:
        calls.append((rpc, params, socket_path))
        return {
            "service_id": params["service_id"],
            "display_name": "Google Drive",
            "configured": True,
            "client_id_configured": True,
            "client_secret_configured": True,
            "token_configured": True,
        }

    monkeypatch.setattr("capabledeputy.cli.main._onguard_call", fake_call)

    result = runner.invoke(
        app,
        [
            "oauth",
            "google",
            "login",
            "google-drive",
            "--no-browser",
            "--timeout",
            "7",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "setup.google.oauth_login",
            {"service_id": "google-drive", "open_browser": False, "timeout_seconds": 7},
            None,
        ),
    ]
    assert "token=yes" in result.stdout


def test_google_oauth_cli_revoke_dispatches_to_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, str | None]] = []

    async def fake_call(rpc: str, params: dict, *, socket_path: str | None) -> dict:
        calls.append((rpc, params, socket_path))
        return {
            "service_id": params["service_id"],
            "display_name": "Google Drive",
            "configured": True,
            "client_id_configured": True,
            "client_secret_configured": True,
            "token_configured": False,
        }

    monkeypatch.setattr("capabledeputy.cli.main._onguard_call", fake_call)

    result = runner.invoke(app, ["oauth", "google", "revoke", "google-drive"])

    assert result.exit_code == 0
    assert calls == [
        ("setup.google.oauth_revoke", {"service_id": "google-drive"}, None),
    ]
    assert "token=no" in result.stdout
