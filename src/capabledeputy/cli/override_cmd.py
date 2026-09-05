"""`capdep override` CLI (003 US6 T080).

Operator-facing surface for the Override workflow. The CLI is the
operator path that invokes request/attest — the planner has no access
to these subcommands (Principle I + V isolation).

The CLI talks to the running daemon over the IPC socket so grants
land in the daemon's persistent OverrideGrantStore (read by
engine.decide() at every dispatch). If the daemon is unavailable, commands
fail explicitly; the client never issues grants into an ephemeral local store.

Subcommands:
  request — initial request (single-authorized: → ACTIVE; dual-
            control: → PENDING_ATTESTATION).
  attest  — distinct attester confirms a PENDING_ATTESTATION grant.
  list    — print all grants in the daemon's store.
  show    — print one grant by id.
  refuse  — explicitly mark a grant REFUSED.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.overrides import HardFloor

override_app = typer.Typer(
    help="Operator override workflow (FR-032/036/038). Distinct from approval.",
    no_args_is_help=True,
)


async def _rpc(method: str, params: dict[str, Any]) -> Any:
    """Make an RPC call to the authoritative daemon."""
    client = DaemonClient(socket_path=default_socket_path())
    return await client.call(method, params)


def _print_grant(grant: dict[str, Any], console: Console) -> None:
    table = Table(show_header=False, box=None)
    table.add_column("field", style="bold cyan")
    table.add_column("value")
    for field, value in grant.items():
        if field == "policy_at_grant" and isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items())
        table.add_row(field, str(value if value is not None else "<none>"))
    console.print(table)


def _print_refusal(refusal: dict[str, Any], console: Console) -> None:
    console.print(f"[red]REFUSED:[/red] {refusal.get('reason', '<unknown>')}")
    if refusal.get("detail"):
        console.print(f"  {refusal['detail']}")


@override_app.command("request")
def request_command(
    session_id: Annotated[str, typer.Option(help="UUID of the session needing the override")],
    action_kind: Annotated[str, typer.Option(help="CapabilityKind value, e.g. SEND_EMAIL")],
    target: Annotated[str, typer.Option(help="Action target (pattern, recipient, etc.)")],
    floor: Annotated[str, typer.Option(help="Hard floor being crossed")],
    invoker: Annotated[str, typer.Option(help="Principal id requesting the override")],
    category: Annotated[str, typer.Option(help="Datum category, e.g. personal")] = "unknown",
    tier: Annotated[str, typer.Option(help="Resolved tier")] = "restricted",
    friction_confirmed: Annotated[
        bool,
        typer.Option(
            "--friction-confirmed",
            help="Acknowledge the typed friction (required for single-authorized + maximal floors)",
        ),
    ] = False,
) -> None:
    """Request an override. Talks to the daemon via IPC so the grant
    lands in the daemon's OverrideGrantStore and engine.decide()
    consults it on the next dispatch."""
    console = Console()
    try:
        UUID(session_id)
        CapabilityKind(action_kind)
        HardFloor(floor)
    except (ValueError, KeyError) as e:
        console.print(f"[red]invalid input:[/red] {e}")
        raise typer.Exit(2) from e
    params = {
        "session_id": session_id,
        "action_kind": action_kind,
        "target": target,
        "floor": floor,
        "invoker": invoker,
        "category": category,
        "tier": tier,
        "friction_confirmed": friction_confirmed,
    }
    result = _dispatch("override.request", params)
    if result.get("refused"):
        _print_refusal(result, console)
        raise typer.Exit(1)
    console.print(f"[green]grant issued:[/green] {result['id']}")
    console.print(f"  state: {result['state']}")
    _print_grant(result, console)


@override_app.command("attest")
def attest_command(
    grant_id: Annotated[str, typer.Option(help="UUID of a PENDING_ATTESTATION grant")],
    attester: Annotated[str, typer.Option(help="Distinct attester principal id")],
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Confirm the attestation (omit to refuse)"),
    ] = False,
) -> None:
    """Dual-control attestation. Goes through the daemon so the
    grant state transition lands where engine.decide() will read it."""
    console = Console()
    try:
        UUID(grant_id)
    except ValueError as e:
        console.print(f"[red]invalid grant_id:[/red] {e}")
        raise typer.Exit(2) from e
    params = {"grant_id": grant_id, "attester": attester, "confirmed": confirm}
    result = _dispatch("override.attest", params)
    if result.get("refused"):
        _print_refusal(result, console)
        raise typer.Exit(1)
    console.print(f"[green]attested:[/green] {result['id']} → {result['state']}")
    _print_grant(result, console)


@override_app.command("list")
def list_command() -> None:
    """List every grant the daemon holds, oldest first."""
    console = Console()
    result = _dispatch("override.list", {})
    grants = result.get("grants", [])
    if not grants:
        console.print("[yellow]no grants[/yellow]")
        return
    table = Table(show_header=True)
    table.add_column("id")
    table.add_column("state")
    table.add_column("session")
    table.add_column("action")
    table.add_column("target")
    table.add_column("invoker")
    table.add_column("expires_at")
    for g in grants:
        table.add_row(
            str(g["id"])[:8],
            g["state"],
            str(g["session_id"])[:8],
            g["action_kind"],
            g["target"],
            g["invoker_principal"],
            g["expires_at"],
        )
    console.print(table)


@override_app.command("show")
def show_command(
    grant_id: Annotated[str, typer.Argument(help="UUID of the grant")],
) -> None:
    console = Console()
    try:
        UUID(grant_id)
    except ValueError as e:
        console.print(f"[red]invalid grant_id:[/red] {e}")
        raise typer.Exit(2) from e
    result = _dispatch("override.show", {"grant_id": grant_id})
    if result.get("refused"):
        _print_refusal(result, console)
        raise typer.Exit(1)
    _print_grant(result, console)


@override_app.command("refuse")
def refuse_command(
    grant_id: Annotated[str, typer.Argument(help="UUID of the grant to refuse")],
) -> None:
    """Mark a grant REFUSED — terminal state."""
    console = Console()
    try:
        UUID(grant_id)
    except ValueError as e:
        console.print(f"[red]invalid grant_id:[/red] {e}")
        raise typer.Exit(2) from e
    result = _dispatch("override.refuse", {"grant_id": grant_id})
    if result.get("refused"):
        _print_refusal(result, console)
        raise typer.Exit(1)
    console.print(f"[yellow]refused:[/yellow] {grant_id}")
    _print_grant(result, console)


# --- dispatch helper -----------------------------------------------


def _dispatch(method: str, params: dict[str, Any]) -> Any:
    """Never substitute client-local authority for an unavailable daemon."""
    try:
        return asyncio.run(_rpc(method, params))
    except DaemonNotRunningError as exc:
        Console(stderr=True).print(
            "[red]Daemon unavailable.[/red] Start the daemon and retry; "
            "no override state was changed."
        )
        raise typer.Exit(1) from exc
