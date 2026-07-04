from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from typer.testing import CliRunner

from capabledeputy.cli.setup_cli import app as setup_app
from capabledeputy.cli.setup_domains import (
    setup_assistant_surface,
    setup_google_workspace_register,
    setup_images,
    setup_imap_register,
    setup_macos_daemon,
    setup_models,
)

runner = CliRunner()


def test_setup_domains_dry_run_does_not_write_real_or_temp_paths(tmp_path: Path) -> None:
    config = tmp_path / "xdg" / "capabledeputy" / "daemon.yaml"
    image_venv = tmp_path / ".venv-images"
    hf_home = tmp_path / "hf"

    results = [
        setup_assistant_surface(config_path=config),
        setup_imap_register(config_path=config),
        setup_google_workspace_register(config_path=config, services="gmail"),
        setup_images(repo_root=tmp_path / "repo", venv_path=image_venv),
        setup_models(cache_home=hf_home),
        setup_macos_daemon(repo_root=tmp_path / "repo"),
    ]

    assert all(result.apply is False for result in results)
    assert not config.exists()
    assert not image_venv.exists()
    assert not hf_home.exists()


def test_setup_domains_apply_uses_injected_paths(tmp_path: Path) -> None:
    config = tmp_path / "xdg" / "capabledeputy" / "daemon.yaml"

    assistant = setup_assistant_surface(apply=True, config_path=config, no_sandbox=True)
    imap = setup_imap_register(apply=True, config_path=config)
    workspace = setup_google_workspace_register(
        apply=True,
        config_path=config,
        services="gmail,calendar",
    )

    text = config.read_text(encoding="utf-8")
    assert assistant.apply is True
    assert imap.apply is True
    assert workspace.apply is True
    assert "bundled-fs" in text
    assert "name: mail" in text
    assert "google-gmail" in text
    assert "google-calendar" in text


def test_setup_images_apply_uses_fake_runner_and_fake_venv(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

    result = setup_images(
        apply=True,
        repo_root=tmp_path / "repo",
        venv_path=tmp_path / "image-venv",
        command_runner=fake_runner,
    )

    assert result.apply is True
    assert calls
    assert all(str(tmp_path) in " ".join(command) for command in calls[:3])


def test_capdep_setup_list_includes_consolidated_domains() -> None:
    result = runner.invoke(setup_app, ["list"])

    assert result.exit_code == 0
    assert "assistant-surface" in result.stdout
    assert "google-cloud" in result.stdout
    assert "images" in result.stdout
    assert "models" in result.stdout
    assert "macos-daemon" in result.stdout


def test_capdep_setup_domains_are_dry_run_by_default(tmp_path: Path) -> None:
    config = tmp_path / "daemon.yaml"
    result = runner.invoke(
        setup_app,
        ["google-workspace", "--services", "gmail", "--config", str(config), "--json"],
    )

    assert result.exit_code == 0
    assert '"apply": false' in result.stdout
    assert '"status": "dry_run"' in result.stdout
    assert not config.exists()


def test_capdep_setup_domain_apply_writes_only_requested_config(tmp_path: Path) -> None:
    config = tmp_path / "daemon.yaml"
    result = runner.invoke(
        setup_app,
        ["imap", "--apply", "--config", str(config), "--json"],
    )

    assert result.exit_code == 0
    assert '"apply": true' in result.stdout
    assert config.exists()
    assert "name: mail" in config.read_text(encoding="utf-8")
