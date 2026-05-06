import os
from pathlib import Path

import pytest

from capabledeputy.ipc.socket_path import default_socket_path


@pytest.fixture(autouse=True)
def _clear_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPDEP_SOCKET", raising=False)


def test_uses_xdg_runtime_dir_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert default_socket_path() == Path("/run/user/1000/capdep.sock")


def test_falls_back_to_tmp_when_xdg_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    expected = Path("/tmp") / f"capdep-{os.getuid()}.sock"
    assert default_socket_path() == expected


def test_capdep_socket_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_SOCKET", "/run/capdep/capdep.sock")
    assert default_socket_path() == Path("/run/capdep/capdep.sock")


def test_capdep_socket_overrides_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_SOCKET", "/var/run/capdep.sock")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert default_socket_path() == Path("/var/run/capdep.sock")
