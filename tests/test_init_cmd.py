"""Unit tests for the `capdep init` onboarding wizard.

Focuses on:
- Non-interactive mode writes correct defaults
- Idempotent: refuses to overwrite without --force
- API key file is mode 0600
- Config dir is XDG-respecting
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from capabledeputy.cli.init_cmd import (
    _sandbox_readiness_line,
    _write_anthropic_key_file,
    _write_config,
)
from capabledeputy.cli.main import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _podman_runner(version_rc: int, info_rc: int):
    import subprocess

    def runner(command):
        rc = version_rc if command[-1] == "--version" else info_rc
        out = "podman version 5.0\n" if command[-1] == "--version" else "{}"
        return subprocess.CompletedProcess(list(command), rc, stdout=out, stderr="")

    return runner


def test_sandbox_readiness_line_surfaces_enabling_command() -> None:
    """#361 — first-run surfaces SEALED reachability and the exact one command
    that enables it, for each Podman state."""
    ready = _sandbox_readiness_line(_podman_runner(0, 0))
    assert "available" in ready and "capdep-setup sandbox --apply" in ready

    down = _sandbox_readiness_line(_podman_runner(0, 1))
    assert "machine is not running" in down and "podman machine start" in down

    absent = _sandbox_readiness_line(_podman_runner(127, 0))
    assert "brew install podman" in absent and "Pattern 3" in absent


def test_non_interactive_writes_defaults(
    tmp_path: Path, runner: CliRunner, monkeypatch: Any
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = runner.invoke(app, ["init", "--non-interactive"])
    assert result.exit_code == 0, result.output
    config_dir = tmp_path / "capabledeputy"
    assert config_dir.is_dir()
    assert (config_dir / "config.yaml").is_file()
    assert (config_dir / "policies").is_dir()


def test_refuses_overwrite_without_force(
    tmp_path: Path,
    runner: CliRunner,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_dir = tmp_path / "capabledeputy"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("# existing\n")

    result = runner.invoke(app, ["init", "--non-interactive"])
    assert result.exit_code == 2
    assert "already exists" in result.output


def test_force_overwrites(tmp_path: Path, runner: CliRunner, monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_dir = tmp_path / "capabledeputy"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("# old content\n")

    result = runner.invoke(app, ["init", "--non-interactive", "--force"])
    assert result.exit_code == 0
    content = (config_dir / "config.yaml").read_text()
    assert "old content" not in content
    assert "balanced" in content  # default risk preference


def test_default_config_disables_social_tools(
    tmp_path: Path,
    runner: CliRunner,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = runner.invoke(app, ["init", "--non-interactive"])
    assert result.exit_code == 0
    text = (tmp_path / "capabledeputy" / "config.yaml").read_text()
    assert "enable_social_commitment: false" in text


def test_api_key_file_mode_is_0600(tmp_path: Path) -> None:
    key_path = _write_anthropic_key_file("sk-test-key", tmp_path)
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"
    assert key_path.read_text(encoding="utf-8").strip() == "sk-test-key"


def test_write_config_minimal_anthropic(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        llm_provider="anthropic",
        anthropic_key_path=tmp_path / "secrets" / "anthropic.key",
        profile="standard",
        risk_preference="cautious",
        enable_social_tools=False,
    )
    text = cfg.read_text()
    assert "provider: anthropic" in text
    assert "anthropic_key_path" in text
    assert "default_profile: standard" in text
    assert "risk_preference: cautious" in text
    assert "enable_social_commitment: false" in text


def test_write_config_anthropic_env_only(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        llm_provider="anthropic",
        anthropic_key_path=None,  # rely on env var
        profile="unrestricted",
        risk_preference="balanced",
        enable_social_tools=True,
    )
    text = cfg.read_text()
    assert "provider: anthropic" in text
    assert "anthropic_key_path" not in text
    assert "ANTHROPIC_API_KEY must be set" in text
    assert "enable_social_commitment: true" in text


def test_write_config_ollama_no_key(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        llm_provider="ollama",
        anthropic_key_path=None,
        profile="unrestricted",
        risk_preference="balanced",
        enable_social_tools=False,
    )
    text = cfg.read_text()
    assert "provider: ollama" in text
    # No anthropic key handling for ollama
    assert "ANTHROPIC_API_KEY" not in text
