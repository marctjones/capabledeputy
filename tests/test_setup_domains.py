from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from typer.testing import CliRunner

from capabledeputy.cli.setup_cli import app as setup_app
from capabledeputy.cli.setup_domains import (
    setup_assistant_surface,
    setup_daily_driver,
    setup_google_workspace_register,
    setup_images,
    setup_imap_register,
    setup_macos_daemon,
    setup_models,
    setup_office_automation,
    setup_sandbox,
)

runner = CliRunner()


def test_setup_domains_dry_run_does_not_write_real_or_temp_paths(tmp_path: Path) -> None:
    config = tmp_path / "xdg" / "capabledeputy" / "daemon.yaml"
    image_venv = tmp_path / ".venv-images"
    hf_home = tmp_path / "hf"

    results = [
        setup_assistant_surface(config_path=config),
        setup_daily_driver(config_path=config, output_dir=tmp_path / "daily-driver"),
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
    assert calls[0][1] == "venv"
    assert calls[1][:3] == (calls[0][0], "pip", "install")
    assert "--python" in calls[1]
    assert calls[2][:3] == (calls[0][0], "pip", "install")
    assert all(str(tmp_path) in " ".join(command) for command in calls[:3])


def test_capdep_setup_list_includes_consolidated_domains() -> None:
    result = runner.invoke(setup_app, ["list"])

    assert result.exit_code == 0
    assert "assistant-surface" in result.stdout
    assert "daily-driver" in result.stdout
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


def test_setup_models_apply_download_uses_fake_runner_and_cache(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

    result = setup_models(
        apply=True,
        download=True,
        cache_home=tmp_path / "hf",
        command_runner=fake_runner,
    )

    assert result.status == "downloaded"
    assert result.changed is True
    assert calls
    assert all(command[:2] == ("hf", "download") for command in calls)
    assert all(str(tmp_path / "hf") in command for command in calls)


def test_setup_models_reports_huggingface_cache_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    hf_home = tmp_path / "hf"
    hf_home.mkdir()
    token_path = hf_home / "token"
    token_path.write_text("hf_fake\n", encoding="utf-8")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

    result = setup_models(cache_home=hf_home)

    assert result.details["hf_token_present"] is True
    assert str(token_path) in result.details["hf_token_sources"]


def test_setup_models_apply_convert_writes_manifests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("capabledeputy.cli.setup_domains.platform.system", lambda: "Darwin")
    monkeypatch.setattr("capabledeputy.cli.setup_domains.platform.machine", lambda: "arm64")

    def fake_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

    result = setup_models(
        apply=True,
        convert=True,
        cache_home=tmp_path / "hf",
        asset_home=tmp_path / "assets",
        command_runner=fake_runner,
    )

    assert result.status == "conversion_manifests_written"
    assert result.changed is True
    assert result.details["inventory"]
    inventory = {item["id"]: item for item in result.details["inventory"]}
    assert inventory["planner.fast"]["download_repo"] == "Qwen/Qwen3-4B-MLX-4bit"
    assert inventory["planner.quality"]["recommended_runtime"] == (
        "mlx-community/Qwen3-30B-A3B-4bit"
    )
    assert inventory["planner.quality.challenger"]["recommended_runtime"] == (
        "mlx-community/Qwen3.6-27B-OptiQ-4bit"
    )
    assert inventory["planner.coder"]["recommended_runtime"] == (
        "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
    )
    assert inventory["image.flux2-klein-quality"]["recommended_runtime"] == (
        "mflux flux2-klein-4b"
    )
    assert inventory["image.qwen-image-quality"]["recommended_runtime"] == (
        "OsaurusAI/Qwen-Image-mflux-4bit"
    )
    assert inventory["vlm.experimental"]["backend"] == "mlx-vlm"
    assert inventory["reranker.default"]["backend"] == "sentence-transformers-cross-encoder"
    assert inventory["reranker.default"]["conversion_status"] == "separate_runtime"
    assert inventory["guard.sidecar"]["recommended_runtime"] == (
        "mlx-community/Qwen3Guard-Gen-0.6B-4bit"
    )
    download_repos = {
        command[2]
        for command in result.details["download_commands"]
        if command[:2] == ["hf", "download"]
    }
    assert "Qwen/Qwen3-4B-MLX-4bit" in download_repos
    assert "mlx-community/Qwen3.6-27B-OptiQ-4bit" in download_repos
    assert "mlx-community/Qwen3-30B-A3B-4bit" in download_repos
    assert "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit" in download_repos
    assert "BAAI/bge-reranker-v2-m3" in download_repos
    assert "mlx-community/Qwen3Guard-Gen-0.6B-4bit" in download_repos
    assert "black-forest-labs/FLUX.2-klein-4B" in download_repos
    assert "OsaurusAI/Qwen-Image-mflux-4bit" in download_repos
    assert "Qwen/Qwen3-4B" not in download_repos
    assert result.details["unsupported_conversions"] == [
        "image.sdxl-photoreal",
        "image.pony-graphic-novel",
    ]
    assert result.details["manifest_paths"]
    assert all(Path(path).is_file() for path in result.details["manifest_paths"])
    assert calls == result.details["conversion_commands"]
    assert all("--quantize" in command for command in result.details["conversion_commands"])
    measured = result.details["measured_quality"]
    assert measured["schema"] == "capdep.model_quality_plan.v1"
    assert measured["retrieval_fixture_count"] == 3
    assert measured["role_benchmark_count"] >= 5
    assert measured["guard_annotation_count"] == 3
    assert {gate["status"] for gate in measured["promotion_gates"]} == {"candidate_only"}


def test_setup_models_download_requires_apply(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="--download requires --apply"):
        setup_models(download=True, cache_home=tmp_path / "hf")


def test_setup_models_convert_requires_apply(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="--convert requires --apply"):
        setup_models(convert=True, cache_home=tmp_path / "hf")


def test_setup_sandbox_apply_uses_fake_podman_runner(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("capabledeputy.cli.setup_domains.shutil.which", lambda name: "/bin/podman")

    def fake_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        stdout = "podman version 5.0\n" if command[-1] == "--version" else "{}"
        return subprocess.CompletedProcess(list(command), 0, stdout=stdout, stderr="")

    result = setup_sandbox(apply=True, command_runner=fake_runner)

    assert result.status == "ready"
    assert calls == [
        ("/bin/podman", "--version"),
        ("/bin/podman", "info", "--format", "json"),
    ]
    assert result.details["runtime_health"]["checked"] is True


def test_setup_macos_daemon_apply_verify_uses_fake_runner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "run-local-daemon-launchd.sh",
        "run-local-daemon-tmux.sh",
        "verify-gui-parity.py",
    ):
        (scripts / name).write_text("#!/bin/sh\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def fake_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        return subprocess.CompletedProcess(list(command), 0, stdout="ok", stderr="")

    result = setup_macos_daemon(
        apply=True,
        verify=True,
        repo_root=repo,
        command_runner=fake_runner,
    )

    assert result.status == "verified"
    assert calls[0] == ("launchctl", "list")
    assert calls[1] == ("ps", "-axo", "pid,command")
    assert calls[2][-1].endswith("verify-gui-parity.py")
    assert result.details["parity_script_present"] is True


def test_setup_office_automation_apply_uses_fake_runner() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        stdout = "/Applications/Fake.app\n" if "com.microsoft.Word" in command[-1] else ""
        return subprocess.CompletedProcess(list(command), 0, stdout=stdout, stderr="")

    result = setup_office_automation(apply=True, command_runner=fake_runner)

    assert result.status == "checked"
    assert result.details["mutates_permissions"] is False
    assert result.details["launches_apps"] is False
    assert any(app["id"] == "microsoft-word" and app["installed"] for app in result.details["apps"])
    assert calls
    assert all(command[0] == "/usr/bin/mdfind" for command in calls)


def test_capdep_setup_rejects_mutating_suboptions_without_apply(tmp_path: Path) -> None:
    models = runner.invoke(setup_app, ["models", "--download", "--hf-home", str(tmp_path)])
    convert = runner.invoke(setup_app, ["models", "--convert", "--hf-home", str(tmp_path)])
    macos = runner.invoke(setup_app, ["macos-daemon", "--verify", "--repo-root", str(tmp_path)])

    assert models.exit_code == 2
    assert convert.exit_code == 2
    assert macos.exit_code == 2
