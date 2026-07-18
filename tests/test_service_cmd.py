"""#318 — `capdep service` CLI: show/install/status/uninstall. install is
write-only by default (never touches launchctl/systemctl unless `--load`), so
these run without mutating the host's service manager."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capabledeputy.cli.service_cmd import service_app

runner = CliRunner()

_SUPPORTED = sys.platform == "darwin" or sys.platform.startswith("linux")
requires_supported = pytest.mark.skipif(
    not _SUPPORTED, reason="service units only generated on macOS/Linux"
)


@requires_supported
def test_show_prints_unit_without_side_effects(tmp_path: Path) -> None:
    unit = tmp_path / "unit.txt"
    result = runner.invoke(service_app, ["show", "--file", str(unit)])
    assert result.exit_code == 0
    # A recognizable slice of either platform's unit.
    assert "KeepAlive" in result.stdout or "Restart=on-failure" in result.stdout
    assert not unit.exists()  # show never writes


@requires_supported
def test_install_dry_run_does_not_write(tmp_path: Path) -> None:
    unit = tmp_path / "unit.txt"
    result = runner.invoke(service_app, ["install", "--dry-run", "--file", str(unit)])
    assert result.exit_code == 0
    assert not unit.exists()


@requires_supported
def test_install_writes_file_and_prints_load_hint_but_does_not_load(tmp_path: Path) -> None:
    unit = tmp_path / "sub" / "unit.txt"
    result = runner.invoke(service_app, ["install", "--file", str(unit)])
    assert result.exit_code == 0
    assert unit.is_file()
    assert unit.read_text()
    # Without --load it only *prints* the load command; it must not have run it.
    assert "to load:" in result.stdout
    assert "launchctl" in result.stdout or "systemctl" in result.stdout
    assert "ran:" not in result.stdout


@requires_supported
def test_status_reflects_install_state(tmp_path: Path) -> None:
    unit = tmp_path / "unit.txt"
    before = runner.invoke(service_app, ["status", "--file", str(unit)])
    assert before.exit_code == 0
    assert "not installed" in before.stdout

    runner.invoke(service_app, ["install", "--file", str(unit)])
    after = runner.invoke(service_app, ["status", "--file", str(unit)])
    assert after.exit_code == 0
    assert "installed" in after.stdout


@requires_supported
def test_uninstall_removes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Never invoke the host's real launchctl/systemctl from the test suite.
    monkeypatch.setattr("capabledeputy.cli.service_cmd._best_effort_load", lambda cmds: None)
    unit = tmp_path / "unit.txt"
    runner.invoke(service_app, ["install", "--file", str(unit)])
    assert unit.is_file()

    result = runner.invoke(service_app, ["uninstall", "--file", str(unit)])
    assert result.exit_code == 0
    assert not unit.exists()


@requires_supported
def test_uninstall_when_absent_is_graceful(tmp_path: Path) -> None:
    # Absent unit short-circuits before any load call — safe without patching.
    unit = tmp_path / "nope.txt"
    result = runner.invoke(service_app, ["uninstall", "--file", str(unit)])
    assert result.exit_code == 0
    assert "not installed" in result.stdout


def test_no_args_shows_help() -> None:
    result = runner.invoke(service_app, [])
    # no_args_is_help -> usage, non-crash.
    assert "supervised" in result.stdout.lower() or "Usage" in result.stdout
