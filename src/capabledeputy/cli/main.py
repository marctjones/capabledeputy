"""Top-level Typer app for the `capdep` command."""

from __future__ import annotations

from typing import Annotated

import anyio
import typer
from rich.console import Console

from capabledeputy.cli.approval import approval_app
from capabledeputy.cli.audit import audit_app, watch_command
from capabledeputy.cli.policy import policy_app
from capabledeputy.cli.session import session_app
from capabledeputy.cli.tool import tool_app
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
app.add_typer(session_app, name="session")
app.add_typer(audit_app, name="audit")
app.add_typer(policy_app, name="policy")
app.add_typer(tool_app, name="tool")
app.add_typer(approval_app, name="approval")
app.command("watch")(watch_command)


@app.command("trace")
def trace_command(
    session_id: Annotated[str, typer.Argument()],
    turn: Annotated[int | None, typer.Option(help="Filter by turn id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Print the trace events for a session, optionally filtered to one turn."""
    import json as _json

    client = DaemonClient(default_socket_path())
    params: dict[str, object] = {"session_id": session_id, "limit": 1000}
    result = anyio.run(client.call, "audit.list", params)
    events = result["events"]
    if turn is not None:
        events = [e for e in events if e.get("turn_id") == turn]

    if json_output:
        console.print(_json.dumps(events, indent=2))
        return

    for ev in events:
        marker = ""
        if ev["event_type"] == "policy.decided":
            decision = ev.get("payload", {}).get("decision", "?")
            color = {"allow": "green", "deny": "red", "require_approval": "yellow"}.get(
                decision,
                "white",
            )
            marker = f" [{color}]{decision}[/{color}]"
        console.print(
            f"[dim]{ev['timestamp']}[/dim] "
            f"[bold]{ev['event_type']}[/bold]{marker}"
            f" turn={ev.get('turn_id')} step={ev.get('step_id')}",
        )


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


@app.command("tui")
def tui_command() -> None:
    """Launch the Textual TUI for live monitoring and approvals."""
    from capabledeputy.tui.app import run

    run()


@app.command("send")
def send_message(
    session_id: str = typer.Argument(..., help="Session id"),
    message: str = typer.Argument(..., help="User message to send"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of rendered output"),
) -> None:
    """Send a user message to a session and run one agent turn."""
    import json as _json

    client = DaemonClient(default_socket_path())
    result = anyio.run(client.call, "session.send", {"session_id": session_id, "message": message})

    if json_output:
        console.print(_json.dumps(result, indent=2))
        return

    console.print(f"[bold]agent:[/bold] {result['content']}")
    console.print(
        f"[dim](iterations={result['iterations']}, finish={result['finish_reason']})[/dim]",
    )
    for outcome in result["tool_outcomes"]:
        color = {"allow": "green", "deny": "red", "require_approval": "yellow"}.get(
            outcome["decision"],
            "white",
        )
        console.print(
            f"  [dim]tool:[/dim] [{color}]{outcome['decision']}[/{color}]"
            + (f" rule={outcome['rule']}" if outcome["rule"] else "")
            + (f" labels+={','.join(outcome['labels_added'])}" if outcome["labels_added"] else ""),
        )


@daemon_app.command("start")
def daemon_start() -> None:
    """Start the daemon in the foreground. Blocks until shutdown."""
    console.print("[green]capdep daemon starting[/green]")
    try:
        anyio.run(run_daemon)
    except KeyboardInterrupt:
        console.print("\n[yellow]capdep daemon stopped (SIGINT)[/yellow]")


@app.command("mcp-server")
def mcp_server_command(
    session_id: str = typer.Option(..., "--session-id", "-s", help="Bound session id"),
    socket: str | None = typer.Option(
        None,
        "--socket",
        help="Override daemon socket path",
    ),
) -> None:
    """Run a stdio MCP server bound to a CapableDeputy session.

    Configure your MCP host (Claude Code, etc.) to launch this command.
    All tool calls from the host go through CapableDeputy's policy engine
    and audit log.
    """
    from pathlib import Path
    from uuid import UUID

    from capabledeputy.mcp_server.server import serve

    sid = UUID(session_id)
    sock = Path(socket) if socket else None
    anyio.run(serve, sid, sock)


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
