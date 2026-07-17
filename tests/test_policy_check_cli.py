"""#385 — `capdep policy check` CLI: exit codes + reporting over a unified doc."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from capabledeputy.cli.policy import policy_app

runner = CliRunner()


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "capdep.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_check_clean_policy_exits_zero(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "posture:\n  id: strict\n  dial: cautious\n"
        "rules:\n  - id: r\n    when: financial + send_email\n    then: deny\n",
    )
    result = runner.invoke(policy_app, ["check", str(p)])
    assert result.exit_code == 0
    assert "passed" in result.stdout


def test_check_reports_errors_and_exits_nonzero(tmp_path: Path) -> None:
    p = _write(tmp_path, "posture:\n  id: p\n  inspectors: [made_up]\n")
    result = runner.invoke(policy_app, ["check", str(p)])
    assert result.exit_code == 1
    assert "unknown inspector" in result.stdout


def test_check_missing_file_uses_defaults_and_passes(tmp_path: Path) -> None:
    result = runner.invoke(policy_app, ["check", str(tmp_path / "absent.yaml")])
    assert result.exit_code == 0
    assert "passed" in result.stdout


def test_check_unparseable_file_exits_config_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "rules: [ : : bad\n")
    result = runner.invoke(policy_app, ["check", str(p)])
    assert result.exit_code == 2
    assert "config error" in result.stdout
