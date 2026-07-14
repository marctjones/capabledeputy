import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capabledeputy.cli.google_cloud_setup import (
    app,
    build_cloud_setup_commands,
    parse_enabled_service_names,
    parse_workspace_services,
    required_cloud_apis,
    run_cloud_setup,
)
from capabledeputy.cli.setup_cli import app as setup_app

runner = CliRunner()


def test_parse_workspace_services_rejects_unknown_service() -> None:
    with pytest.raises(ValueError, match="unknown Google Workspace service"):
        parse_workspace_services("gmail,photos")


def test_required_cloud_apis_are_deduplicated_in_service_order() -> None:
    assert required_cloud_apis(("gmail", "calendar", "people")) == (
        "gmail.googleapis.com",
        "gmailmcp.googleapis.com",
        "calendar-json.googleapis.com",
        "calendarmcp.googleapis.com",
        "people.googleapis.com",
    )


def test_build_cloud_setup_commands_can_create_and_link_project() -> None:
    commands = build_cloud_setup_commands(
        project_id="capdep-oauth",
        apis=("gmail.googleapis.com", "gmailmcp.googleapis.com"),
        create_project=True,
        project_name="CapDep OAuth",
        organization="123456",
        folder=None,
        billing_account="ABC-DEF-GHI",
    )

    assert commands == (
        (
            "gcloud",
            "projects",
            "create",
            "capdep-oauth",
            "--name",
            "CapDep OAuth",
            "--organization",
            "123456",
        ),
        (
            "gcloud",
            "billing",
            "projects",
            "link",
            "capdep-oauth",
            "--billing-account",
            "ABC-DEF-GHI",
        ),
        ("gcloud", "config", "set", "project", "capdep-oauth"),
        (
            "gcloud",
            "services",
            "enable",
            "gmail.googleapis.com",
            "gmailmcp.googleapis.com",
            "--project",
            "capdep-oauth",
        ),
    )


def test_run_cloud_setup_dry_run_skips_commands_without_gcloud() -> None:
    result = run_cloud_setup(project_id="capdep-oauth", services="gmail")

    assert result.apply is False
    assert result.ran_commands == ()
    assert result.skipped_commands == result.commands
    assert result.cloud_apis == ("gmail.googleapis.com", "gmailmcp.googleapis.com")
    assert result.oauth_clients_url.endswith("project=capdep-oauth")


def test_run_cloud_setup_apply_uses_injected_runner() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_runner(command: Sequence[str]):
        calls.append(tuple(command))
        output = "[]"
        if tuple(command[:3]) == ("gcloud", "services", "list"):
            output = (
                '[{"config":{"name":"gmail.googleapis.com"}},'
                '{"config":{"name":"gmailmcp.googleapis.com"}},'
                '{"config":{"name":"drive.googleapis.com"}},'
                '{"config":{"name":"drivemcp.googleapis.com"}}]'
            )
        return subprocess.CompletedProcess(list(command), 0, stdout=output, stderr="")

    result = run_cloud_setup(
        project_id="capdep-oauth",
        services="gmail,drive",
        apply=True,
        command_runner=fake_runner,
    )

    assert result.ran_commands == tuple(calls[:-1])
    assert result.skipped_commands == ()
    assert calls[-2] == (
        "gcloud",
        "services",
        "enable",
        "gmail.googleapis.com",
        "gmailmcp.googleapis.com",
        "drive.googleapis.com",
        "drivemcp.googleapis.com",
        "--project",
        "capdep-oauth",
    )
    assert calls[-1] == (
        "gcloud",
        "services",
        "list",
        "--enabled",
        "--project",
        "capdep-oauth",
        "--format=json",
    )
    assert result.missing_cloud_apis == ()


def test_run_cloud_setup_apply_fails_if_required_api_still_missing() -> None:
    def fake_runner(command: Sequence[str]):
        output = "[]"
        if tuple(command[:3]) == ("gcloud", "services", "list"):
            output = '[{"config":{"name":"gmail.googleapis.com"}}]'
        return subprocess.CompletedProcess(list(command), 0, stdout=output, stderr="")

    with pytest.raises(RuntimeError, match=r"gmailmcp\.googleapis\.com"):
        run_cloud_setup(
            project_id="capdep-oauth",
            services="gmail",
            apply=True,
            command_runner=fake_runner,
        )


def test_parse_enabled_service_names_accepts_gcloud_json() -> None:
    assert parse_enabled_service_names(
        '[{"config":{"name":"gmail.googleapis.com"}},{"name":"drive.googleapis.com"}]'
    ) == ("gmail.googleapis.com", "drive.googleapis.com")


def test_run_cloud_setup_can_register_capdep_managed_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = run_cloud_setup(
        project_id="capdep-oauth",
        services="gmail,calendar",
        register_capdep=True,
    )

    assert result.local_config_changed is True
    assert result.local_config_path == str(tmp_path / "capabledeputy" / "daemon.yaml")
    text = Path(str(result.local_config_path)).read_text(encoding="utf-8")
    assert "google-gmail" in text
    assert "google-calendar" in text
    assert "google-drive" not in text


def test_google_cloud_setup_cli_dry_run_prints_manual_steps() -> None:
    result = runner.invoke(
        app,
        [
            "--project",
            "capdep-oauth",
            "--services",
            "gmail,calendar",
        ],
    )

    assert result.exit_code == 0
    assert "Google Cloud setup plan" in result.stdout
    assert "gmail.googleapis.com" in result.stdout
    assert "calendarmcp.googleapis.com" in result.stdout
    assert "verification" in result.stdout
    assert "Manual Google steps that remain" in result.stdout
    assert "capdep oauth google configure" in result.stdout
    assert "google-gmail --client-id" in result.stdout


def test_google_cloud_setup_cli_json_dry_run() -> None:
    result = runner.invoke(
        app,
        [
            "--project",
            "capdep-oauth",
            "--services",
            "gmail",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"project_id": "capdep-oauth"' in result.stdout
    assert '"gmailmcp.googleapis.com"' in result.stdout


def test_generic_setup_cli_lists_available_setup_domains() -> None:
    result = runner.invoke(setup_app, ["list"])

    assert result.exit_code == 0
    assert "google-cloud" in result.stdout
    assert "Workspace OAuth" in result.stdout


def test_generic_setup_cli_dispatches_google_cloud_setup() -> None:
    result = runner.invoke(
        setup_app,
        [
            "google-cloud",
            "--project",
            "capdep-oauth",
            "--services",
            "gmail",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"project_id": "capdep-oauth"' in result.stdout
    assert '"gmailmcp.googleapis.com"' in result.stdout
