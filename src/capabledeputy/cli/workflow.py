"""Daemon-owned workflow template launcher for CLI parity."""

from __future__ import annotations

from typing import Annotated, Any

import anyio
import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.daemon.workflow_templates import workflow_turn_message
from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

workflow_app = typer.Typer(help="Run daemon-owned workflow templates.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


def _client() -> DaemonClient:
    return DaemonClient(default_socket_path())


def _call(method: str, params: dict[str, Any] | None = None) -> Any:
    return anyio.run(_client().call, method, params or {})


@workflow_app.command("list")
def workflow_list(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List daemon-owned workflow templates."""
    result = _call("workflow.templates", {})
    templates = result.get("templates", [])
    if json_output:
        console.print_json(data=templates)
        return
    table = Table(title=f"Workflow templates ({len(templates)})")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Purpose")
    table.add_column("Foreground review")
    for template in templates:
        table.add_row(
            template.get("id", ""),
            template.get("title", ""),
            template.get("purpose_handle", ""),
            "yes" if template.get("requires_foreground_review") else "no",
        )
    console.print(table)


@workflow_app.command("run")
def workflow_run(
    template_id: Annotated[str, typer.Argument(help="Workflow template id")],
    client_id: Annotated[str, typer.Option("--client-id", help="Client id for turn tracking")] = "capdep-cli",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a session and start the workflow prompt via session.turn.start."""
    result = _call("workflow.templates", {})
    template = next(
        (item for item in result.get("templates", []) if item.get("id") == template_id),
        None,
    )
    if template is None:
        err_console.print(f"[red]unknown workflow template:[/red] {template_id}")
        raise typer.Exit(code=1)

    turn_message = template.get("turn_message") or workflow_turn_message(template)
    session = _call(
        "session.new",
        {
            "intent": turn_message,
            "owner": client_id,
            "purpose_handle": template.get("purpose_handle", "general"),
        },
    )
    session_id = session.get("id", "")
    turn_result = _call(
        "session.turn.start",
        {
            "session_id": session_id,
            "message": turn_message,
            "client_id": client_id,
        },
    )
    if json_output:
        console.print_json(
            data={
                "template": template,
                "session": session,
                "turn": turn_result.get("turn", {}),
            },
        )
        return
    turn = turn_result.get("turn", {})
    console.print(
        f"[green]started workflow[/green] {template.get('title', template_id)} "
        f"session={session_id[:8]} turn={turn.get('id', '')[:8]}",
    )
    console.print(
        "[dim]Use `capdep session turn events <turn_id>` or `capdep chat` to follow progress.[/dim]",
    )