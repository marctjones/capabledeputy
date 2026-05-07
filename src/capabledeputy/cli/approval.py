"""capdep approval: queue, inspect, and decide on approval requests."""

from __future__ import annotations

from typing import Annotated, Any

import anyio
import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

approval_app = typer.Typer(help="Manage approval requests.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


def _client() -> DaemonClient:
    return DaemonClient(default_socket_path())


def _call(method: str, params: dict[str, Any] | None = None) -> Any:
    return anyio.run(_client().call, method, params or {})


@approval_app.command("list")
def approval_list(
    status: Annotated[str | None, typer.Option(help="Filter by status")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List approval requests."""
    params: dict[str, Any] = {}
    if status:
        params["status"] = status
    result = _call("approval.list", params)
    approvals = result["approvals"]

    if json_output:
        console.print_json(data=approvals)
        return

    table = Table(title=f"Approvals ({len(approvals)})")
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Action")
    table.add_column("Target")
    table.add_column("From session")
    table.add_column("Labels in")
    for r in approvals:
        table.add_row(
            str(r["id"]),
            r["status"],
            r["action"],
            r["target"],
            r["from_session"][:8],
            ", ".join(r["labels_in"]),
        )
    console.print(table)


@approval_app.command("show")
def approval_show(
    request_id: Annotated[int, typer.Argument(help="Approval id")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show details of one approval request including the verbatim payload."""
    result = _call("approval.show", {"id": request_id})
    if json_output:
        console.print_json(data=result)
        return
    console.print(
        f"[bold]approval #{result['id']}[/bold] status=[yellow]{result['status']}[/yellow]",
    )
    console.print(f"  action:        {result['action']}")
    console.print(f"  target:        {result['target']}")
    console.print(f"  from session:  {result['from_session']}")
    if result.get("to_session"):
        console.print(f"  to session:    {result['to_session']}")
    console.print(f"  labels in:     {', '.join(result['labels_in']) or '(none)'}")
    console.print(f"  justification: {result['justification']}")
    console.print(f"  requested at:  {result['requested_at']}")
    console.print("  payload:")
    for line in result["payload"].split("\n"):
        console.print(f"    {line}")


@approval_app.command("submit")
def approval_submit(
    from_session: Annotated[str, typer.Option("--from-session")],
    action: Annotated[str, typer.Option("--action", help="SEND_EMAIL, DECLASSIFY, ...")],
    payload: Annotated[str, typer.Option("--payload", help="Verbatim text to be sent")],
    target: Annotated[str, typer.Option("--target", help="Recipient/destination")],
    label: Annotated[
        list[str] | None,
        typer.Option("--label", help="Label of incoming data (repeatable)"),
    ] = None,
    justification: Annotated[str, typer.Option("--justification")] = "",
) -> None:
    """Submit a new approval request."""
    result = _call(
        "approval.submit",
        {
            "from_session": from_session,
            "action": action,
            "payload": payload,
            "target": target,
            "labels_in": label or [],
            "justification": justification,
        },
    )
    console.print(f"[green]submitted approval #{result['id']}[/green] (status={result['status']})")


@approval_app.command("approve")
def approval_approve(
    request_id: Annotated[int, typer.Argument(help="Approval id")],
    decided_by: Annotated[str, typer.Option("--by")] = "user",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Approve a request and execute it (spawning a purpose-limited session if needed)."""
    result = _call(
        "approval.approve",
        {"id": request_id, "decided_by": decided_by},
    )
    if json_output:
        console.print_json(data=result)
        return
    console.print(f"[green]approval #{request_id} approved[/green]")
    if "executed_in_session" in result:
        console.print(f"  executed in session: {result['executed_in_session']}")
        console.print(f"  dispatch decision:   {result['dispatch']['decision']}")
        if result["dispatch"]["error"]:
            err_console.print(f"  dispatch error:      {result['dispatch']['error']}")


@approval_app.command("deny")
def approval_deny(
    request_id: Annotated[int, typer.Argument()],
    reason: Annotated[str, typer.Option("--reason")] = "",
) -> None:
    """Deny an approval request."""
    _call("approval.deny", {"id": request_id, "reason": reason})
    console.print(f"[red]approval #{request_id} denied[/red]")


@approval_app.command("defer")
def approval_defer(request_id: Annotated[int, typer.Argument()]) -> None:
    """Defer an approval request."""
    _call("approval.defer", {"id": request_id})
    console.print(f"[yellow]approval #{request_id} deferred[/yellow]")


pattern_app = typer.Typer(
    help="Manage approval pattern rules (auto-approve matching requests).",
    no_args_is_help=True,
)
approval_app.add_typer(pattern_app, name="pattern")


@pattern_app.command("list")
def pattern_list(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List active approval pattern rules."""
    result = _call("approval_pattern.list")
    patterns = result["patterns"]
    if json_output:
        console.print_json(data=patterns)
        return
    table = Table(title=f"Approval pattern rules ({len(patterns)})")
    table.add_column("ID")
    table.add_column("Action")
    table.add_column("Target pattern")
    table.add_column("Expires")
    table.add_column("Uses")
    table.add_column("Revoked")
    for p in patterns:
        table.add_row(
            p["id"][:8],
            p["action"],
            p["target_pattern"],
            p["expires_at"],
            str(p["auto_approval_count"]),
            "yes" if p["revoked"] else "no",
        )
    console.print(table)


@pattern_app.command("create")
def pattern_create(
    action: Annotated[str, typer.Option("--action")],
    target: Annotated[str, typer.Option("--target", help="Target pattern (specific or *@domain)")],
    ttl_hours: Annotated[float, typer.Option("--ttl-hours")] = 24.0,
) -> None:
    """Create a new pattern rule."""
    result = _call(
        "approval_pattern.create",
        {"action": action, "target_pattern": target, "ttl_hours": ttl_hours},
    )
    if "error" in result:
        err_console.print(f"[red]invalid pattern: {result['error']}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]created pattern rule {result['id']}[/green]")
    console.print(f"  expires:        {result['expires_at']}")


@pattern_app.command("revoke")
def pattern_revoke(
    pattern_id: Annotated[str, typer.Argument()],
) -> None:
    """Revoke a pattern rule."""
    result = _call("approval_pattern.revoke", {"id": pattern_id})
    if "error" in result:
        err_console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[yellow]pattern {pattern_id[:8]} revoked[/yellow]")
