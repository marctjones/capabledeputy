"""Top-level setup automation entry point for CapDep."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from capabledeputy.cli.google_cloud_setup import app as google_cloud_app
from capabledeputy.cli.setup_domains import (
    SetupDomainResult,
    result_to_json,
    setup_assistant_surface,
    setup_google_workspace_register,
    setup_images,
    setup_imap_register,
    setup_macos_daemon,
    setup_models,
    setup_office_automation,
    setup_sandbox,
)

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    help=(
        "One-time CapDep setup automation. These commands prepare external "
        "accounts, machine-local assets, and optional integrations without "
        "adding configuration workflows to the main capdep command."
    ),
    no_args_is_help=True,
)

app.add_typer(
    google_cloud_app,
    name="google-cloud",
    help="Prepare Google Cloud and Workspace API access for CapDep OAuth.",
)


def _print_result(result: SetupDomainResult, *, json_output: bool) -> None:
    if json_output:
        typer.echo(result_to_json(result))
        return
    mode = "apply" if result.apply else "dry-run"
    console.print(f"[bold]{result.domain} setup[/bold] ([bold]{mode}[/bold])")
    console.print(result.summary)
    for key, value in result.paths.items():
        console.print(f"  [dim]{key}:[/dim] {value}")
    if result.actions:
        console.print("\n[bold]actions[/bold]")
        for action in result.actions:
            console.print(f"  [dim]-[/dim] {action}")
    if result.commands:
        console.print("\n[bold]commands[/bold]")
        for command in result.commands:
            console.print("  " + " ".join(command))
    if result.details:
        console.print("\n[bold]details[/bold]")
        for key, value in result.details.items():
            console.print(f"  [dim]{key}:[/dim] {value}")
    if not result.apply:
        console.print(
            "\n[dim]No local state was changed. "
            "Re-run with --apply to mutate setup state.[/dim]",
        )


@app.command("list")
def list_setups() -> None:
    """List available setup automation domains."""
    typer.echo("assistant-surface\tBundled assistant MCP server config bootstrap")
    typer.echo("google-cloud\tGoogle Cloud / Workspace OAuth API enablement")
    typer.echo("google-workspace\tGoogle Workspace daemon config registration")
    typer.echo("images\tImage-generation runtime venv setup")
    typer.echo("imap\tIMAP daemon config registration")
    typer.echo("macos-daemon\tmacOS daemon launch path and parity validation")
    typer.echo("models\tLocal model recommendation and harvesting plan")
    typer.echo("office-automation\tNative desktop Office app readiness checks")
    typer.echo("sandbox\tSandbox prerequisite verification")


@app.command("assistant-surface")
def assistant_surface_command(
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Write managed daemon config blocks."),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Daemon config path. Defaults to user-local daemon.yaml."),
    ] = None,
    no_sandbox: Annotated[
        bool,
        typer.Option("--no-sandbox", help="Do not register a sandbox block."),
    ] = False,
    force_sandbox: Annotated[
        bool,
        typer.Option(
            "--force-sandbox",
            help="Register the sandbox block even if Podman is absent.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Prepare the bundled assistant MCP server surface."""
    try:
        result = setup_assistant_surface(
            apply=apply,
            config_path=config,
            no_sandbox=no_sandbox,
            force_sandbox=force_sandbox,
        )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None
    _print_result(result, json_output=json_output)


@app.command("imap")
def imap_command(
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Write the IMAP managed daemon block."),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Daemon config path. Defaults to user-local daemon.yaml."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Register the IMAP MCP server block without collecting credentials."""
    _print_result(setup_imap_register(apply=apply, config_path=config), json_output=json_output)


@app.command("google-workspace")
def google_workspace_command(
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Write the Google Workspace managed daemon block."),
    ] = False,
    mode: Annotated[
        str,
        typer.Option("--mode", help="Integration mode: official or community."),
    ] = "official",
    services: Annotated[
        str,
        typer.Option("--services", "-s", help="Comma-separated service list."),
    ] = "",
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Daemon config path. Defaults to user-local daemon.yaml."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Register Google Workspace MCP daemon config."""
    try:
        result = setup_google_workspace_register(
            apply=apply,
            config_path=config,
            mode=mode,
            services=services,
        )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None
    _print_result(result, json_output=json_output)


@app.command("images")
def images_command(
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Create/update the isolated image runtime venv."),
    ] = False,
    repo_root: Annotated[Path | None, typer.Option("--repo-root", help="Repository root.")] = None,
    venv: Annotated[Path | None, typer.Option("--venv", help="Image runtime venv path.")] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Plan or apply image-generation runtime setup."""
    _print_result(
        setup_images(apply=apply, repo_root=repo_root, venv_path=venv),
        json_output=json_output,
    )


@app.command("models")
def models_command(
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Plan model harvesting with explicit apply semantics."),
    ] = False,
    hf_home: Annotated[
        Path | None,
        typer.Option("--hf-home", help="Hugging Face cache home."),
    ] = None,
    download: Annotated[
        bool,
        typer.Option("--download", help="Download recommended assets. Requires --apply."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Inspect local capability and recommend model assets."""
    try:
        result = setup_models(apply=apply, download=download, cache_home=hf_home)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None
    _print_result(result, json_output=json_output)


@app.command("sandbox")
def sandbox_command(
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Reserved for future mutating setup."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Check sandbox prerequisites."""
    _print_result(setup_sandbox(apply=apply), json_output=json_output)


@app.command("office-automation")
def office_automation_command(
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Run local app availability checks."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Check native Office automation prerequisites without launching apps."""
    _print_result(setup_office_automation(apply=apply), json_output=json_output)


@app.command("macos-daemon")
def macos_daemon_command(
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Reserved for future launchd changes."),
    ] = False,
    repo_root: Annotated[Path | None, typer.Option("--repo-root", help="Repository root.")] = None,
    verify: Annotated[
        bool,
        typer.Option("--verify", help="Run daemon connectivity/parity checks. Requires --apply."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Check macOS daemon launch paths and parity validation prerequisites."""
    try:
        result = setup_macos_daemon(apply=apply, repo_root=repo_root, verify=verify)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None
    _print_result(result, json_output=json_output)


if __name__ == "__main__":
    app()
