"""Google Cloud setup helper for CapDep Workspace OAuth."""

from __future__ import annotations

import json
import shutil
import subprocess
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.cli._managed_config import (
    GWORKSPACE_BLOCK_ID,
    GWORKSPACE_DEFAULT_OFFICIAL_SERVICES,
    google_workspace_official_block_body,
    user_default_daemon_config_path,
    write_managed_block,
)

RunCommand = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

WORKSPACE_SERVICE_APIS: dict[str, tuple[str, ...]] = {
    "gmail": ("gmail.googleapis.com", "gmailmcp.googleapis.com"),
    "drive": ("drive.googleapis.com", "drivemcp.googleapis.com"),
    "calendar": ("calendar-json.googleapis.com", "calendarmcp.googleapis.com"),
    "chat": ("chat.googleapis.com", "chatmcp.googleapis.com"),
    "people": ("people.googleapis.com",),
}

OAUTH_OVERVIEW_URL = "https://console.cloud.google.com/auth/overview?project={project}"
OAUTH_CLIENTS_URL = "https://console.cloud.google.com/auth/clients?project={project}"
WORKSPACE_API_CONTROLS_URL = "https://admin.google.com/ac/owl"


@dataclass(frozen=True)
class CloudSetupResult:
    project_id: str
    services: tuple[str, ...]
    cloud_apis: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]
    verification_command: tuple[str, ...]
    ran_commands: tuple[tuple[str, ...], ...]
    skipped_commands: tuple[tuple[str, ...], ...]
    verified_cloud_apis: tuple[str, ...]
    missing_cloud_apis: tuple[str, ...]
    local_config_path: str | None
    local_config_changed: bool
    oauth_overview_url: str
    oauth_clients_url: str
    workspace_admin_url: str
    apply: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "services": list(self.services),
            "cloud_apis": list(self.cloud_apis),
            "commands": [list(command) for command in self.commands],
            "verification_command": list(self.verification_command),
            "ran_commands": [list(command) for command in self.ran_commands],
            "skipped_commands": [list(command) for command in self.skipped_commands],
            "verified_cloud_apis": list(self.verified_cloud_apis),
            "missing_cloud_apis": list(self.missing_cloud_apis),
            "local_config_path": self.local_config_path,
            "local_config_changed": self.local_config_changed,
            "oauth_overview_url": self.oauth_overview_url,
            "oauth_clients_url": self.oauth_clients_url,
            "workspace_admin_url": self.workspace_admin_url,
            "apply": self.apply,
        }


def parse_workspace_services(services: str) -> tuple[str, ...]:
    requested = tuple(s.strip().lower() for s in services.split(",") if s.strip())
    if not requested:
        return tuple(s.strip() for s in GWORKSPACE_DEFAULT_OFFICIAL_SERVICES.split(","))
    unknown = [service for service in requested if service not in WORKSPACE_SERVICE_APIS]
    if unknown:
        expected = ", ".join(WORKSPACE_SERVICE_APIS)
        unknown_services = ", ".join(unknown)
        raise ValueError(
            f"unknown Google Workspace service(s): {unknown_services}. Expected: {expected}"
        )
    return requested


def required_cloud_apis(services: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    apis: list[str] = []
    for service in services:
        for api in WORKSPACE_SERVICE_APIS[service]:
            if api not in seen:
                seen.add(api)
                apis.append(api)
    return tuple(apis)


def build_cloud_setup_commands(
    *,
    project_id: str,
    apis: Sequence[str],
    create_project: bool,
    project_name: str,
    organization: str | None,
    folder: str | None,
    billing_account: str | None,
) -> tuple[tuple[str, ...], ...]:
    commands: list[tuple[str, ...]] = []
    if create_project:
        create = ["gcloud", "projects", "create", project_id, "--name", project_name]
        if organization:
            create.extend(["--organization", organization])
        if folder:
            create.extend(["--folder", folder])
        commands.append(tuple(create))
    if billing_account:
        commands.append(
            (
                "gcloud",
                "billing",
                "projects",
                "link",
                project_id,
                "--billing-account",
                billing_account,
            )
        )
    commands.append(("gcloud", "config", "set", "project", project_id))
    commands.append(("gcloud", "services", "enable", *apis, "--project", project_id))
    return tuple(commands)


def build_enabled_services_command(project_id: str) -> tuple[str, ...]:
    return (
        "gcloud",
        "services",
        "list",
        "--enabled",
        "--project",
        project_id,
        "--format=json",
    )


def parse_enabled_service_names(output: str) -> tuple[str, ...]:
    if not output.strip():
        return ()
    raw = json.loads(output)
    if not isinstance(raw, list):
        raise ValueError("gcloud services list returned non-list JSON")
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("config", {}).get("name") or item.get("name") or "")
            if name:
                names.append(name)
    return tuple(names)


def run_cloud_setup(
    *,
    project_id: str,
    services: str = GWORKSPACE_DEFAULT_OFFICIAL_SERVICES,
    create_project: bool = False,
    project_name: str = "CapDep Google Workspace OAuth",
    organization: str | None = None,
    folder: str | None = None,
    billing_account: str | None = None,
    apply: bool = False,
    register_capdep: bool = False,
    command_runner: RunCommand | None = None,
) -> CloudSetupResult:
    project_id = project_id.strip()
    if not project_id:
        raise ValueError("project id is required")
    if organization and folder:
        raise ValueError("use either --organization or --folder, not both")

    parsed_services = parse_workspace_services(services)
    apis = required_cloud_apis(parsed_services)
    commands = build_cloud_setup_commands(
        project_id=project_id,
        apis=apis,
        create_project=create_project,
        project_name=project_name,
        organization=organization,
        folder=folder,
        billing_account=billing_account,
    )
    verification_command = build_enabled_services_command(project_id)

    ran: list[tuple[str, ...]] = []
    skipped: list[tuple[str, ...]] = []
    verified: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    if apply:
        if shutil.which("gcloud") is None and command_runner is None:
            raise RuntimeError("gcloud is not on PATH; install Google Cloud CLI first")
        runner = command_runner or _default_runner
        for command in commands:
            runner(command)
            ran.append(command)
        verification = runner(verification_command)
        verified = parse_enabled_service_names(verification.stdout or "")
        verified_set = set(verified)
        missing = tuple(api for api in apis if api not in verified_set)
        if missing:
            raise RuntimeError(
                "Google Cloud project is still missing required enabled API(s): "
                + ", ".join(missing)
            )
    else:
        skipped.extend(commands)

    local_config_path: str | None = None
    local_config_changed = False
    if register_capdep:
        block_body = google_workspace_official_block_body(",".join(parsed_services))
        daemon_yaml = user_default_daemon_config_path()
        _replaced, local_config_changed = write_managed_block(
            daemon_yaml,
            GWORKSPACE_BLOCK_ID,
            block_body,
        )
        local_config_path = str(daemon_yaml)

    return CloudSetupResult(
        project_id=project_id,
        services=parsed_services,
        cloud_apis=apis,
        commands=commands,
        verification_command=verification_command,
        ran_commands=tuple(ran),
        skipped_commands=tuple(skipped),
        verified_cloud_apis=verified,
        missing_cloud_apis=missing,
        local_config_path=local_config_path,
        local_config_changed=local_config_changed,
        oauth_overview_url=OAUTH_OVERVIEW_URL.format(project=project_id),
        oauth_clients_url=OAUTH_CLIENTS_URL.format(project=project_id),
        workspace_admin_url=WORKSPACE_API_CONTROLS_URL,
        apply=apply,
    )


def print_cloud_setup_result(
    result: CloudSetupResult,
    *,
    console: Console,
    json_output: bool = False,
) -> None:
    if json_output:
        console.print(json.dumps(result.as_dict(), indent=2))
        return

    mode = "applied" if result.apply else "dry run"
    console.print(f"[bold]Google Cloud setup plan[/bold] ([bold]{mode}[/bold])")
    console.print(f"Project: [bold]{result.project_id}[/bold]")
    console.print(f"Workspace services: {', '.join(result.services)}")
    console.print()

    table = Table(title="gcloud commands")
    table.add_column("Status")
    table.add_column("Command")
    ran = set(result.ran_commands)
    for command in result.commands:
        status = "ran" if command in ran else "pending"
        table.add_row(status, " ".join(command))
    console.print(table)

    console.print("\n[bold]verification[/bold]")
    if result.apply:
        console.print("  [green]enabled APIs verified[/green]")
    else:
        console.print("  pending: " + " ".join(result.verification_command))

    if result.local_config_path:
        changed = "updated" if result.local_config_changed else "already current"
        console.print(f"[green]CapDep config {changed}:[/green] {result.local_config_path}")

    console.print("\n[bold]Manual Google steps that remain[/bold]")
    console.print(f"1. Configure/publish the OAuth consent app: {result.oauth_overview_url}")
    console.print(f"2. Create an OAuth client and copy its ID/secret: {result.oauth_clients_url}")
    console.print(
        "3. For Workspace domains, trust or allow that OAuth client in Admin API controls: "
        f"{result.workspace_admin_url}"
    )
    console.print(
        "4. Store the client in CapDep, for example: "
        "[bold]capdep oauth google configure google-gmail "
        "--client-id ... --client-secret ...[/bold]"
    )


def open_cloud_setup_pages(result: CloudSetupResult) -> None:
    for url in (
        result.oauth_overview_url,
        result.oauth_clients_url,
        result.workspace_admin_url,
    ):
        webbrowser.open(url)


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=True,
        text=True,
        capture_output=True,
    )


def cloud_setup_command(
    project_id: str,
    services: str,
    create_project: bool,
    project_name: str,
    organization: str | None,
    folder: str | None,
    billing_account: str | None,
    apply: bool,
    register_capdep: bool,
    open_pages: bool,
    json_output: bool,
    console: Console,
    err_console: Console,
) -> None:
    try:
        result = run_cloud_setup(
            project_id=project_id,
            services=services,
            create_project=create_project,
            project_name=project_name,
            organization=organization,
            folder=folder,
            billing_account=billing_account,
            apply=apply,
            register_capdep=register_capdep,
        )
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    print_cloud_setup_result(result, console=console, json_output=json_output)
    if open_pages:
        open_cloud_setup_pages(result)


app = typer.Typer(
    help=(
        "One-time Google Cloud setup helper for CapDep Workspace OAuth. "
        "Dry-runs by default; use --apply to ensure APIs/MCP services are enabled."
    ),
    no_args_is_help=False,
)
console = Console()
err_console = Console(stderr=True)


@app.callback(invoke_without_command=True)
def main(
    project_id: Annotated[
        str,
        typer.Option(
            "--project",
            "-p",
            help="Google Cloud project ID to configure for CapDep Workspace OAuth.",
        ),
    ],
    services: Annotated[
        str,
        typer.Option(
            "--services",
            "-s",
            help="Comma-separated Workspace services: gmail,drive,calendar,chat,people.",
        ),
    ] = "gmail,drive,calendar",
    create_project: Annotated[
        bool,
        typer.Option("--create-project", help="Create the Google Cloud project before setup."),
    ] = False,
    project_name: Annotated[
        str,
        typer.Option("--project-name", help="Display name when --create-project is used."),
    ] = "CapDep Google Workspace OAuth",
    organization: Annotated[
        str | None,
        typer.Option("--organization", help="Google Cloud organization ID for project creation."),
    ] = None,
    folder: Annotated[
        str | None,
        typer.Option("--folder", help="Google Cloud folder ID for project creation."),
    ] = None,
    billing_account: Annotated[
        str | None,
        typer.Option("--billing-account", help="Billing account to link after project creation."),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Run gcloud commands and verify required APIs are enabled."),
    ] = False,
    register_capdep: Annotated[
        bool,
        typer.Option(
            "--register-capdep",
            help="Write/update CapDep's local Google Workspace managed config block.",
        ),
    ] = False,
    open_pages: Annotated[
        bool,
        typer.Option("--open", help="Open the remaining Google Auth/Admin console pages."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a setup summary."),
    ] = False,
) -> None:
    cloud_setup_command(
        project_id=project_id,
        services=services,
        create_project=create_project,
        project_name=project_name,
        organization=organization,
        folder=folder,
        billing_account=billing_account,
        apply=apply,
        register_capdep=register_capdep,
        open_pages=open_pages,
        json_output=json_output,
        console=console,
        err_console=err_console,
    )


if __name__ == "__main__":
    app()
