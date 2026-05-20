"""`capdep override` CLI (003 US6 T080).

Operator-facing surface for the Override workflow. The CLI is the
ONLY path that invokes request/attest — the planner has no access
to these subcommands (Principle I + V isolation).

Subcommands:
  request — initial request (single-authorized: → ACTIVE; dual-
            control: → PENDING_ATTESTATION).
  attest  — distinct attester confirms a PENDING_ATTESTATION grant.
  list    — print all grants in the in-memory store.
  show    — print one grant by id.
  refuse  — explicitly mark a grant REFUSED (e.g., attester declines).

The CLI operates on an in-memory OverrideGrantStore today. A
persistent backing store (override_grants table) is a follow-up;
for demos and CI this is enough.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.overrides import (
    GrantState,
    HardFloor,
    OverrideGrant,
    OverrideGrantStore,
    OverridePolicies,
    OverridePolicy,
    OverridePolicyEntry,
    OverrideRefusal,
    attest_override,
    request_override,
)

override_app = typer.Typer(
    help="Operator override workflow (FR-032/036/038). Distinct from approval.",
    no_args_is_help=True,
)


# Module-level singleton for the CLI session. Tests inject their own
# stores via `_set_test_doubles()`. Production usage holds the store
# on the daemon and threads it through; this file's standalone CLI
# path supports the demo + audit-replay scenarios.
_STORE: OverrideGrantStore = OverrideGrantStore()
_POLICIES: OverridePolicies = OverridePolicies(by_floor={})


def _set_test_doubles(
    *,
    store: OverrideGrantStore | None = None,
    policies: OverridePolicies | None = None,
) -> None:
    """Test hook: swap in alternative store/policies for one test run."""
    global _STORE, _POLICIES
    if store is not None:
        _STORE = store
    if policies is not None:
        _POLICIES = policies


def _get_store() -> OverrideGrantStore:
    return _STORE


def _get_policies() -> OverridePolicies:
    return _POLICIES


def _print_grant(grant: OverrideGrant, console: Console) -> None:
    table = Table(show_header=False, box=None)
    table.add_column("field", style="bold cyan")
    table.add_column("value")
    table.add_row("id", str(grant.id))
    table.add_row("session_id", str(grant.session_id))
    table.add_row("action_kind", grant.action_kind.value)
    table.add_row("target", grant.target)
    table.add_row("floor", grant.hard_floor_crossed.value)
    table.add_row("invoker", grant.invoker_principal)
    table.add_row("attester", grant.attester_principal or "<none>")
    table.add_row("state", grant.state.value)
    table.add_row("friction", grant.friction_level.value)
    table.add_row("expires_at", grant.expires_at.isoformat())
    table.add_row("consumed_at", grant.consumed_at.isoformat() if grant.consumed_at else "<none>")
    console.print(table)


@override_app.command("request")
def request_command(
    session_id: Annotated[str, typer.Option(help="UUID of the session needing the override")],
    action_kind: Annotated[str, typer.Option(help="CapabilityKind value, e.g. send_email")],
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
    """Request an override. On success, prints the grant id (and the
    state — ACTIVE for single-authorized, PENDING_ATTESTATION for
    dual-control)."""
    console = Console()
    try:
        session_uuid = UUID(session_id)
        kind = CapabilityKind(action_kind)
        hard_floor = HardFloor(floor)
    except (ValueError, KeyError) as e:
        console.print(f"[red]invalid input:[/red] {e}")
        raise typer.Exit(2) from e
    result = request_override(
        policies=_get_policies(),
        session_id=session_uuid,
        action_kind=kind,
        target=target,
        target_category_tier=(category, tier),
        floor=hard_floor,
        invoker=invoker,
        friction_confirmed=friction_confirmed,
    )
    if isinstance(result, OverrideRefusal):
        console.print(f"[red]REFUSED:[/red] {result.reason.value}")
        if result.detail:
            console.print(f"  {result.detail}")
        raise typer.Exit(1)
    _get_store().add(result)
    console.print(f"[green]grant issued:[/green] {result.id}")
    console.print(f"  state: {result.state.value}")
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
    """Dual-control attestation. Attester must differ from invoker
    AND be in the policy's `attester_principal_ids`."""
    console = Console()
    try:
        gid = UUID(grant_id)
    except ValueError as e:
        console.print(f"[red]invalid grant_id:[/red] {e}")
        raise typer.Exit(2) from e
    grant = _get_store().get(gid)
    if grant is None:
        console.print(f"[red]unknown grant:[/red] {grant_id}")
        raise typer.Exit(1)
    result = attest_override(grant, attester=attester, confirmed=confirm)
    if isinstance(result, OverrideRefusal):
        console.print(f"[red]REFUSED:[/red] {result.reason.value}")
        if result.detail:
            console.print(f"  {result.detail}")
        raise typer.Exit(1)
    _get_store().update(result)
    console.print(f"[green]attested:[/green] {result.id} → {result.state.value}")
    _print_grant(result, console)


@override_app.command("list")
def list_command() -> None:
    """List every grant in the store, oldest first."""
    console = Console()
    grants = _get_store().list_all()
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
    for g in sorted(grants, key=lambda g: g.expires_at):
        table.add_row(
            str(g.id)[:8],
            g.state.value,
            str(g.session_id)[:8],
            g.action_kind.value,
            g.target,
            g.invoker_principal,
            g.expires_at.isoformat(),
        )
    console.print(table)


@override_app.command("show")
def show_command(
    grant_id: Annotated[str, typer.Argument(help="UUID of the grant")],
) -> None:
    console = Console()
    try:
        gid = UUID(grant_id)
    except ValueError as e:
        console.print(f"[red]invalid grant_id:[/red] {e}")
        raise typer.Exit(2) from e
    grant = _get_store().get(gid)
    if grant is None:
        console.print(f"[red]unknown grant:[/red] {grant_id}")
        raise typer.Exit(1)
    _print_grant(grant, console)


@override_app.command("refuse")
def refuse_command(
    grant_id: Annotated[str, typer.Argument(help="UUID of the grant to refuse")],
) -> None:
    """Mark a grant REFUSED (e.g., attester declines). Terminal state."""
    from dataclasses import replace

    console = Console()
    try:
        gid = UUID(grant_id)
    except ValueError as e:
        console.print(f"[red]invalid grant_id:[/red] {e}")
        raise typer.Exit(2) from e
    grant = _get_store().get(gid)
    if grant is None:
        console.print(f"[red]unknown grant:[/red] {grant_id}")
        raise typer.Exit(1)
    refused = replace(grant, state=GrantState.REFUSED)
    _get_store().update(refused)
    console.print(f"[yellow]refused:[/yellow] {grant_id}")
    _print_grant(refused, console)


# Suppress unused-import warnings for items only used by docstring
# references in the CLI surface.
_ = OverridePolicy
_ = OverridePolicyEntry
_ = datetime
_ = UTC
