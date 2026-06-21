"""capdep tool: inspect registered tools and simulate dispatch."""

from __future__ import annotations

from typing import Annotated, Any

import anyio
import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

tool_app = typer.Typer(
    help="Inspect and test registered tools.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _client() -> DaemonClient:
    return DaemonClient(default_socket_path())


def _call(method: str, params: dict[str, Any] | None = None) -> Any:
    return anyio.run(_client().call, method, params or {})


def _parse_arg(spec: str) -> tuple[str, str]:
    key, sep, value = spec.partition("=")
    if not sep:
        raise typer.BadParameter(f"invalid arg spec '{spec}', expected KEY=VALUE")
    return key, value


@tool_app.command("list")
def tool_list(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table"),
    ] = False,
) -> None:
    """List all registered tools."""
    result = _call("tool.list")
    tools = result["tools"]

    if json_output:
        console.print_json(data=tools)
        return

    table = Table(title=f"Tools ({len(tools)})")
    table.add_column("Name")
    table.add_column("Capability")
    table.add_column("Target arg")
    table.add_column("Amount arg")
    table.add_column("Inherent labels")
    for tool in tools:
        table.add_row(
            tool["name"],
            tool["capability_kind"],
            tool["target_arg"],
            tool["amount_arg"] or "",
            ", ".join(tool["inherent_labels"]),
        )
    console.print(table)


@tool_app.command("show")
def tool_show(
    name: Annotated[str, typer.Argument(help="Tool name")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of rendered output"),
    ] = False,
) -> None:
    """Show detailed metadata for one tool."""
    result = _call("tool.show", {"name": name})
    if json_output:
        console.print_json(data=result)
        return
    console.print(f"[bold]{result['name']}[/bold]")
    console.print(f"  description:     {result['description']}")
    console.print(f"  capability_kind: {result['capability_kind']}")
    console.print(f"  target_arg:      {result['target_arg']}")
    if result["amount_arg"]:
        console.print(f"  amount_arg:      {result['amount_arg']}")
    if result["inherent_labels"]:
        console.print(f"  inherent_labels: {', '.join(result['inherent_labels'])}")


@tool_app.command("test")
def tool_test(
    tool: Annotated[str, typer.Option("--tool", help="Tool name")],
    session: Annotated[str, typer.Option("--session", help="Session id")],
    arg: Annotated[
        list[str] | None,
        typer.Option("--arg", help="Tool arg KEY=VALUE (repeatable)"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a rendered view"),
    ] = False,
) -> None:
    """Simulate calling a tool: returns the policy decision without dispatching."""
    args: dict[str, Any] = dict(_parse_arg(spec) for spec in (arg or []))
    result = _call(
        "tool.test",
        {"tool": tool, "session_id": session, "args": args},
    )

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


@tool_app.command("call")
def tool_call(
    tool: Annotated[str, typer.Option("--tool", help="Tool name")],
    session: Annotated[str, typer.Option("--session", help="Session id")],
    arg: Annotated[
        list[str] | None,
        typer.Option("--arg", help="Tool arg KEY=VALUE (repeatable)"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of rendered output"),
    ] = False,
) -> None:
    """Call a daemon tool through the policy chokepoint.

    This is intentionally not a direct tool invocation. The daemon still
    performs policy evaluation, approval queuing, provenance, and audit.
    """
    args: dict[str, Any] = dict(_parse_arg(spec) for spec in (arg or []))
    result = _call(
        "tool.call",
        {"tool": tool, "session_id": session, "args": args},
    )

    if json_output:
        console.print_json(data=result)
        return

    color = {
        "allow": "green",
        "deny": "red",
        "require_approval": "yellow",
    }.get(result.get("decision"), "white")
    console.print(f"[{color}]decision: {result.get('decision', '?')}[/{color}]")
    if result.get("approval_id") is not None:
        console.print(f"approval id: {result['approval_id']}")
    if result.get("rule"):
        console.print(f"rule: {result['rule']}")
    if result.get("reason"):
        console.print(f"reason: {result['reason']}")
    if result.get("error"):
        err_console.print(f"[red]error:[/red] {result['error']}")
    if result.get("output") is not None:
        console.print(result["output"])
