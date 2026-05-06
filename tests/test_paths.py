from pathlib import Path

import pytest

from capabledeputy.paths import (
    default_audit_log_path,
    default_data_dir,
    default_state_db_path,
)


@pytest.fixture(autouse=True)
def _clear_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPDEP_DATA_DIR", raising=False)
    monkeypatch.delenv("CAPDEP_STATE_DB", raising=False)
    monkeypatch.delenv("CAPDEP_AUDIT_LOG", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


def test_data_dir_capdep_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_DATA_DIR", "/var/lib/capdep")
    assert default_data_dir() == Path("/var/lib/capdep")


def test_data_dir_capdep_override_beats_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_DATA_DIR", "/var/lib/capdep")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/me/.local/share")
    assert default_data_dir() == Path("/var/lib/capdep")


def test_data_dir_falls_back_to_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", "/home/me/.local/share")
    assert default_data_dir() == Path("/home/me/.local/share/capabledeputy")


def test_data_dir_falls_back_to_home() -> None:
    expected = Path.home() / ".local" / "share" / "capabledeputy"
    assert default_data_dir() == expected


def test_state_db_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_STATE_DB", "/var/lib/capdep/state.db")
    assert default_state_db_path() == Path("/var/lib/capdep/state.db")


def test_state_db_uses_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPDEP_DATA_DIR", str(tmp_path))
    assert default_state_db_path() == tmp_path / "state.db"


def test_state_db_override_beats_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPDEP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAPDEP_STATE_DB", "/var/lib/capdep.db")
    assert default_state_db_path() == Path("/var/lib/capdep.db")


def test_audit_log_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_AUDIT_LOG", "/var/log/capdep.jsonl")
    assert default_audit_log_path() == Path("/var/log/capdep.jsonl")


def test_audit_log_uses_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPDEP_DATA_DIR", str(tmp_path))
    assert default_audit_log_path() == tmp_path / "audit.jsonl"
