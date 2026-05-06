"""capdep audit + capdep watch: query and stream the audit log."""

from __future__ import annotations

import contextlib
from typing import Annotated, Any

import anyio
import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

audit_app = typer.Typer(
    help="Inspect the audit log.",
    no_args_is_help=False,
    invoke_without_command=True,
)
console = Console()


def _client() -> DaemonClient:
    return DaemonClient(default_socket_path())


def _short(s: str | None, n: int = 8) -> str:
    return s[:n] if s else ""


def _render_event_row(table: Table, ev: dict[str, Any]) -> None:
    table.add_row(
        ev["timestamp"],
        ev["event_type"],
        _short(ev.get("session_id")),
        _short(ev.get("audit_id"), 8),
    )


@audit_app.callback()
def audit_main(
    ctx: typer.Context,
    event_type: Annotated[
        str | None,
        typer.Option("--type", help="Filter by event type, e.g. session.created"),
    ] = None,
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Filter by session id"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option(help="Maximum number of events to return"),
    ] = 50,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table"),
    ] = False,
) -> None:
    """Query historical audit events."""
    if ctx.invoked_subcommand is not None:
        return

    params: dict[str, Any] = {"limit": limit}
    if event_type:
        params["event_type"] = event_type
    if session_id:
        params["session_id"] = session_id

    result = anyio.run(_client().call, "audit.list", params)
    events = result["events"]

    if json_output:
        console.print_json(data=events)
        return

    table = Table(title=f"Audit events ({len(events)})")
    table.add_column("Timestamp")
    table.add_column("Type")
    table.add_column("Session")
    table.add_column("Audit ID")
    for ev in events:
        _render_event_row(table, ev)
    console.print(table)


def watch_command(
    poll_interval: Annotated[
        float,
        typer.Option(help="Seconds between polls of the daemon"),
    ] = 0.5,
    event_type: Annotated[
        str | None,
        typer.Option("--type", help="Filter by event type"),
    ] = None,
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Filter by session id"),
    ] = None,
) -> None:
    """Stream audit events as they happen. Ctrl-C to exit."""
    client = _client()

    async def loop() -> None:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor is not None:
                params["after_audit_id"] = cursor
            result = await client.call("audit.tail", params)
            for ev in result["events"]:
                if event_type and ev["event_type"] != event_type:
                    continue
                if session_id and ev.get("session_id") != session_id:
                    continue
                console.print(
                    f"[dim]{ev['timestamp']}[/dim] "
                    f"[bold]{ev['event_type']}[/bold] "
                    f"session={_short(ev.get('session_id'))} "
                    f"audit={_short(ev.get('audit_id'))}",
                )
                cursor = ev["audit_id"]
            await anyio.sleep(poll_interval)

    with contextlib.suppress(KeyboardInterrupt):
        anyio.run(loop)
