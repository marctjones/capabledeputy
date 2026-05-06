"""capdep policy: inspect and simulate the policy engine."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

import anyio
import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

policy_app = typer.Typer(
    help="Inspect and test the policy engine.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _client() -> DaemonClient:
    return DaemonClient(default_socket_path())


def _call(method: str, params: dict[str, Any] | None = None) -> Any:
    return anyio.run(_client().call, method, params or {})


def _parse_capability_spec(spec: str) -> dict[str, Any]:
    kind, sep, pattern = spec.partition(":")
    if not sep or not pattern:
        raise typer.BadParameter(
            f"invalid capability spec '{spec}', expected KIND:pattern",
        )
    return {
        "kind": kind,
        "pattern": pattern,
        "expiry": "session",
        "origin": "system_default",
        "audit_id": str(uuid4()),
    }


@policy_app.command("show")
def policy_show(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of tables"),
    ] = False,
) -> None:
    """Show the current label set, capability kinds, and conflict rules."""
    result = _call("policy.show")

    if json_output:
        console.print_json(data=result)
        return

    label_table = Table(title=f"Labels ({len(result['labels'])})")
    label_table.add_column("Label")
    for label in result["labels"]:
        label_table.add_row(label)
    console.print(label_table)

    kind_table = Table(title=f"Capability kinds ({len(result['capability_kinds'])})")
    kind_table.add_column("Kind")
    for kind in result["capability_kinds"]:
        kind_table.add_row(kind)
    console.print(kind_table)

    rule_table = Table(title=f"Conflict rules ({len(result['rules'])})")
    rule_table.add_column("Name")
    rule_table.add_column("Triggers")
    rule_table.add_column("Conflicts")
    rule_table.add_column("Decision")
    for rule in result["rules"]:
        rule_table.add_row(
            rule["name"],
            ", ".join(rule["triggers"]),
            ", ".join(rule["conflicts"]),
            rule["decision"],
        )
    console.print(rule_table)


@policy_app.command("validate")
def policy_validate() -> None:
    """Validate the active policy bundle's invariants."""
    result = _call("policy.validate")
    if result["valid"]:
        console.print("[green]policy is valid[/green]")
        return
    console.print("[red]policy is invalid[/red]")
    for err in result["errors"]:
        err_console.print(f"  - {err}")
    raise typer.Exit(code=1)


@policy_app.command("test")
def policy_test(
    action: Annotated[str, typer.Option("--action", help="Capability kind, e.g. SEND_EMAIL")],
    target: Annotated[str, typer.Option("--target", help="Action target (path/recipient/url)")],
    amount: Annotated[
        int | None,
        typer.Option("--amount", help="For QUEUE_PURCHASE actions"),
    ] = None,
    labels: Annotated[
        list[str] | None,
        typer.Option("--label", help="Label in the session (repeatable)"),
    ] = None,
    capability: Annotated[
        list[str] | None,
        typer.Option(
            "--capability",
            help="Capability KIND:pattern (repeatable)",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a rendered view"),
    ] = False,
) -> None:
    """Simulate a policy decision for a given session state and action."""
    cap_specs = [_parse_capability_spec(spec) for spec in (capability or [])]
    params: dict[str, Any] = {
        "action_kind": action,
        "target": target,
        "labels": labels or [],
        "capabilities": cap_specs,
    }
    if amount is not None:
        params["amount"] = amount

    result = _call("policy.test", params)

    if json_output:
        console.print_json(data=result)
        return

    color = {
        "allow": "green",
        "deny": "red",
        "require_approval": "yellow",
    }.get(result["decision"], "white")
    console.print(f"[{color}]decision: {result['decision']}[/{color}]")
    if result["rule"]:
        console.print(f"rule: {result['rule']}")
    if result["reason"]:
        console.print(f"reason: {result['reason']}")
    if result["effective_labels"]:
        console.print(f"effective labels: {', '.join(result['effective_labels'])}")
    matched = result["matched_capability"]
    if matched:
        console.print(f"matched capability: {matched['kind']}({matched['pattern']})")
