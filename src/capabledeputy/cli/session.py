"""capdep session subcommand: lifecycle operations on the session graph."""

from __future__ import annotations

import json as json_mod
from typing import Annotated, Any

import anyio
import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

session_app = typer.Typer(
    help="Inspect and manipulate the session graph.",
    no_args_is_help=True,
)
console = Console()


def _client() -> DaemonClient:
    return DaemonClient(default_socket_path())


def _call(method: str, params: dict[str, Any] | None = None) -> Any:
    return anyio.run(_client().call, method, params or {})


def _short_id(s: str) -> str:
    return s[:8]


def _render_session(s: dict[str, Any]) -> None:
    console.print(f"[bold]session[/bold] {s['id']}")
    console.print(f"  status:  {s['status']}")
    if s.get("parent"):
        console.print(f"  parent:  {s['parent']}")
    if s.get("intent"):
        console.print(f"  intent:  {s['intent']}")
    if s.get("owner"):
        console.print(f"  owner:   {s['owner']}")
    console.print(f"  created: {s['created_at']}")
    console.print(f"  updated: {s['updated_at']}")
    if s["label_set"]:
        console.print(f"  labels:  {', '.join(s['label_set'])}")
    if s["capability_set"]:
        console.print(f"  caps:    {', '.join(s['capability_set'])}")
    if s.get("tool_aliasing"):
        console.print("  flags:   tool_aliasing")
    if s.get("prefer_programmatic"):
        console.print("  flags:   prefer_programmatic")


@session_app.command("list")
def session_list(
    status: Annotated[
        str | None,
        typer.Option(help="Filter by status (active, paused, done, aborted, ...)"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table"),
    ] = False,
) -> None:
    """List all sessions in the graph."""
    params: dict[str, Any] = {}
    if status:
        params["status"] = status
    result = _call("session.list", params)
    sessions = result["sessions"]

    if json_output:
        console.print_json(data=sessions)
        return

    table = Table(title=f"Sessions ({len(sessions)})")
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Parent")
    table.add_column("Intent")
    table.add_column("Updated")
    for s in sessions:
        table.add_row(
            _short_id(s["id"]),
            s["status"],
            _short_id(s["parent"]) if s.get("parent") else "",
            s["intent"] or "",
            s["updated_at"],
        )
    console.print(table)


@session_app.command("new")
def session_new(
    intent: Annotated[str | None, typer.Option(help="Why this session exists")] = None,
    owner: Annotated[str | None, typer.Option(help="Owner identifier")] = None,
    tool_tokens: Annotated[
        bool,
        typer.Option(
            "--tool-tokens",
            help=(
                "Show the LLM session-specific aliases instead of canonical "
                "tool names (strict ocap)."
            ),
        ),
    ] = False,
    prefer_programmatic: Annotated[
        bool,
        typer.Option(
            "--prefer-programmatic",
            help=(
                "Run agent turns in programmatic mode "
                "(LLM emits a Python program, not tool calls)."
            ),
        ),
    ] = False,
) -> None:
    """Create a new top-level session."""
    params: dict[str, Any] = {}
    if intent:
        params["intent"] = intent
    if owner:
        params["owner"] = owner
    if tool_tokens:
        params["tool_aliasing"] = True
    if prefer_programmatic:
        params["prefer_programmatic"] = True
    s = _call("session.new", params)
    _render_session(s)


@session_app.command("fork")
def session_fork(
    parent_id: Annotated[str, typer.Argument(help="Parent session id")],
    intent: Annotated[str | None, typer.Option(help="Why this branch exists")] = None,
) -> None:
    """Fork a session into a child."""
    params: dict[str, Any] = {"parent_id": parent_id}
    if intent:
        params["intent"] = intent
    s = _call("session.fork", params)
    _render_session(s)


@session_app.command("pause")
def session_pause(
    session_id: Annotated[str, typer.Argument(help="Session id to pause")],
) -> None:
    """Pause an active session."""
    s = _call("session.pause", {"session_id": session_id})
    _render_session(s)


@session_app.command("resume")
def session_resume(
    session_id: Annotated[str, typer.Argument(help="Session id to resume")],
) -> None:
    """Resume a paused session."""
    s = _call("session.resume", {"session_id": session_id})
    _render_session(s)


@session_app.command("abort")
def session_abort(
    session_id: Annotated[str, typer.Argument(help="Session id to abort")],
) -> None:
    """Abort a non-terminal session."""
    s = _call("session.abort", {"session_id": session_id})
    _render_session(s)


@session_app.command("show")
def session_show(
    session_id: Annotated[str, typer.Argument(help="Session id to show")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a rendered view"),
    ] = False,
) -> None:
    """Show one session in detail."""
    s = _call("session.get", {"session_id": session_id})
    if json_output:
        console.print(json_mod.dumps(s, indent=2))
    else:
        _render_session(s)
