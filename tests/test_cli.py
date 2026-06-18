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
