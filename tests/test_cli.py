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


def test_image_profiles_cli_dispatches_to_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, str | None]] = []

    async def fake_call(rpc: str, params: dict, *, socket_path: str | None) -> dict:
        calls.append((rpc, params, socket_path))
        return {
            "selected": "default",
            "profiles": [
                {
                    "id": "default",
                    "backend": "mflux",
                    "model": "z-image-turbo",
                    "steps": 9,
                    "tier": "fast",
                    "recommended": True,
                    "benchmark_note": "fast default",
                },
            ],
        }

    monkeypatch.setattr("capabledeputy.cli.image._call", fake_call)

    result = runner.invoke(app, ["image", "profiles", "--socket", "/tmp/capdep.sock"])

    assert result.exit_code == 0
    assert calls == [("image.profiles", {}, "/tmp/capdep.sock")]
    assert "z-image-turbo" in result.stdout


def test_image_profile_cli_sets_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, str | None]] = []

    async def fake_call(rpc: str, params: dict, *, socket_path: str | None) -> dict:
        calls.append((rpc, params, socket_path))
        return {
            "selected": "balanced",
            "changed": ["image_profile"],
            "readiness": {
                "ok": True,
                "profile": "balanced",
                "backend": "mflux",
                "model": "z-image-turbo",
                "checks": [],
            },
        }

    monkeypatch.setattr("capabledeputy.cli.image._call", fake_call)

    result = runner.invoke(app, ["image", "profile", "balanced"])

    assert result.exit_code == 0
    assert calls == [("image.profile.set", {"profile": "balanced"}, None)]
    assert "selected image profile" in result.stdout


def test_image_readiness_cli_dispatches_to_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, str | None]] = []

    async def fake_call(rpc: str, params: dict, *, socket_path: str | None) -> dict:
        calls.append((rpc, params, socket_path))
        return {
            "ok": False,
            "profile": "quality",
            "backend": "mflux",
            "model": "flux2-klein-4b",
            "checks": [
                {
                    "id": "mflux",
                    "status": "error",
                    "detail": "mflux not importable",
                    "recovery": "Install image extras.",
                },
            ],
        }

    monkeypatch.setattr("capabledeputy.cli.image._call", fake_call)

    result = runner.invoke(app, ["image", "readiness", "--profile", "quality"])

    assert result.exit_code == 0
    assert calls == [("image.readiness", {"profile": "quality"}, None)]
    assert "not ready" in result.stdout


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
