import os
from pathlib import Path

import pytest

from capabledeputy.ipc.socket_path import default_socket_path


def test_uses_xdg_runtime_dir_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert default_socket_path() == Path("/run/user/1000/capdep.sock")


def test_falls_back_to_tmp_when_xdg_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    expected = Path("/tmp") / f"capdep-{os.getuid()}.sock"
    assert default_socket_path() == expected
