"""#322 — capdep doctor: health checks degrade gracefully and collapse to an
overall status/exit code."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from capabledeputy import diagnostics as dx
from capabledeputy.cli.main import app

runner = CliRunner()


def test_state_db_absent_is_ok(tmp_path: Path) -> None:
    c = dx.check_state_db(tmp_path / "nope.db")
    assert c.status == "ok" and "fresh install" in c.detail


def test_state_db_good_is_ok(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("create table t (x int)")
    con.commit()
    con.close()
    assert dx.check_state_db(db).status == "ok"


def test_state_db_corrupt_is_fail(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    db.write_bytes(b"this is not a sqlite database file, at all")
    assert dx.check_state_db(db).status == "fail"


def test_llm_key_reflects_env(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert dx.check_llm_key().status == "ok"


def test_configs_fail_on_missing_dir(tmp_path: Path) -> None:
    assert dx.check_configs(tmp_path / "no-such-dir").status == "fail"


def test_overall_status_precedence() -> None:
    ok = dx.Check("a", "ok", "")
    warn = dx.Check("b", "warn", "")
    fail = dx.Check("c", "fail", "")
    assert dx.overall_status([ok, ok]) == "ok"
    assert dx.overall_status([ok, warn]) == "warn"
    assert dx.overall_status([ok, warn, fail]) == "fail"


async def test_check_daemon_not_running_is_warn(tmp_path: Path) -> None:
    c = await dx.check_daemon(tmp_path / "absent.sock")
    assert c.status == "warn" and "not running" in c.detail


def test_doctor_cli_runs_and_reports() -> None:
    # No daemon / possibly no key in CI -> warnings, but never a crash; exit 0
    # unless a hard check fails.
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (0, 1)
    assert "daemon" in result.stdout
    assert "configs" in result.stdout
    assert "state-db" in result.stdout
