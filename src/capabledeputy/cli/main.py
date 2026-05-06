"""Top-level Typer app for the `capdep` command."""

from __future__ import annotations

import anyio
import typer
from rich.console import Console

from capabledeputy.daemon.lifecycle import (
    daemon_status,
    run_daemon,
    stop_daemon,
)
from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.version import __version__

app = typer.Typer(
    help="CapableDeputy: a capable deputy, never a confused one.",
    no_args_is_help=True,
)
daemon_app = typer.Typer(help="Manage the CapableDeputy daemon.", no_args_is_help=True)
app.add_typer(daemon_app, name="daemon")

console = Console()
err_console = Console(stderr=True)


@app.command()
def version() -> None:
    """Print the CapableDeputy version. Round-trips through the daemon if running."""
    client = DaemonClient(default_socket_path())
    try:
        result = anyio.run(client.call, "version")
        console.print(f"capdep {result['version']} (via daemon)")
    except DaemonNotRunningError:
        console.print(f"capdep {__version__} (daemon not running)")


@daemon_app.command("start")
def daemon_start() -> None:
    """Start the daemon in the foreground. Blocks until shutdown."""
    console.print("[green]capdep daemon starting[/green]")
    try:
        anyio.run(run_daemon)
    except KeyboardInterrupt:
        console.print("\n[yellow]capdep daemon stopped (SIGINT)[/yellow]")


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Stop a running daemon by sending a shutdown RPC."""
    stopped = anyio.run(stop_daemon)
    if stopped:
        console.print("[green]daemon stopped[/green]")
    else:
        err_console.print("[red]daemon not running[/red]")
        raise typer.Exit(code=1)


@daemon_app.command("status")
def daemon_status_cmd() -> None:
    """Report whether the daemon is running."""
    status = anyio.run(daemon_status)
    if status["running"]:
        console.print("[green]daemon running[/green]")
    else:
        console.print("[yellow]daemon not running[/yellow]")
        raise typer.Exit(code=1)
