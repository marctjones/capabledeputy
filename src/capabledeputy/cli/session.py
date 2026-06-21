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
                "Run agent turns in programmatic mode (LLM emits a Python program, not tool calls)."
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


@session_app.command("children")
def session_children(
    session_id: Annotated[str, typer.Argument(help="Parent session id")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table"),
    ] = False,
) -> None:
    """List child sessions delegated from a parent session."""
    result = _call("session.children", {"session_id": session_id})
    sessions = result["sessions"]
    if json_output:
        console.print_json(data=sessions)
        return
    table = Table(title=f"Child sessions ({len(sessions)})")
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Intent")
    for s in sessions:
        table.add_row(_short_id(s["id"]), s["status"], s.get("intent") or "")
    console.print(table)


@session_app.command("revoke")
def session_revoke(
    session_id: Annotated[str, typer.Argument(help="Session that holds the capability")],
    audit_id: Annotated[str, typer.Argument(help="Capability audit_id to revoke")],
    trigger: Annotated[
        str,
        typer.Option("--trigger", help="Why this revocation (audited)"),
    ] = "operator-revoke",
) -> None:
    """002 US2 — revoke a capability by audit_id within a session.

    The cascade is computed lazily at the next decide(); any descendant
    that traces back to this audit_id via parent_audit_id will be
    denied with `capability-cascaded` and pending approvals authorized
    by that descendant will be invalidated at approve-time.

    Operator-only; the AI cannot invoke this.
    """
    params = {
        "session_id": session_id,
        "audit_id": audit_id,
        "trigger": trigger,
    }
    s = _call("capability.revoke", params)
    console.print(
        f"[green]revoked[/green] audit_id={audit_id} in session={session_id[:8]} trigger={trigger}",
    )
    _render_session(s)


@session_app.command("delegate")
def session_delegate(
    parent_id: Annotated[str, typer.Argument(help="Parent session id")],
    child_id: Annotated[str, typer.Argument(help="Child session id")],
    kind: Annotated[str, typer.Option("--kind", help="CapabilityKind to delegate")],
    pattern: Annotated[
        str | None,
        typer.Option("--pattern", help="Narrower target pattern (must be a subset)"),
    ] = None,
    max_amount: Annotated[
        int | None,
        typer.Option("--max-amount", help="Lower amount cap (≤ parent)"),
    ] = None,
    ttl_seconds: Annotated[
        int | None,
        typer.Option("--ttl-seconds", help="Earlier expiry, seconds from now"),
    ] = None,
    expiry: Annotated[
        str | None,
        typer.Option("--expiry", help="Lifetime: one_shot|session|persistent"),
    ] = None,
) -> None:
    """Delegate an attenuated capability from a parent to a child
    session. The engine derives the (clamped) capability; every
    broadening request is refused. The model can never call this with a
    pre-built capability — only this narrowing request."""
    from datetime import UTC, datetime, timedelta

    params: dict[str, Any] = {
        "parent_session_id": parent_id,
        "child_session_id": child_id,
        "kind": kind,
    }
    if pattern is not None:
        params["pattern"] = pattern
    if max_amount is not None:
        params["max_amount"] = max_amount
    if ttl_seconds is not None:
        params["expires_at"] = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
    if expiry is not None:
        params["expiry"] = expiry
    result = _call("session.delegate", params)
    if result.get("granted"):
        cap = result["capability"]
        console.print(
            f"[green]delegated[/green] {cap['kind']} "
            f"pattern={cap['pattern']} depth={cap['depth']} "
            f"audit_id={cap['audit_id']}",
        )
    else:
        console.print(f"[red]refused[/red] reason={result.get('reason')}")


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


@session_app.command("cancel")
def session_cancel(
    session_id: Annotated[str, typer.Argument(help="Session id with an active turn")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of rendered output"),
    ] = False,
) -> None:
    """Cancel an active agent turn for a session."""
    result = _call("session.cancel", {"session_id": session_id})
    if json_output:
        console.print_json(data=result)
        return
    if result.get("cancelled"):
        console.print(f"[yellow]cancelled active turn for {session_id[:8]}[/yellow]")
    else:
        console.print(f"[dim]no active turn for {session_id[:8]}[/dim]")


@session_app.command("add-labels")
def session_add_labels(
    session_id: Annotated[str, typer.Argument(help="Session id")],
    labels: Annotated[
        list[str],
        typer.Option("--label", help="Label to add (repeatable)"),
    ],
) -> None:
    """Add labels to a session through the daemon."""
    s = _call("session.add_labels", {"session_id": session_id, "labels": labels})
    _render_session(s)


@session_app.command("set-enforcement")
def session_set_enforcement(
    session_id: Annotated[str, typer.Argument(help="Session id")],
    mode: Annotated[str, typer.Option("--mode", help="enforce|shadow")],
) -> None:
    """Set a session's daemon enforcement posture."""
    s = _call("session.set_enforcement", {"session_id": session_id, "mode": mode})
    _render_session(s)


@session_app.command("first-use-prompts")
def session_first_use_prompts(
    session_id: Annotated[str, typer.Argument(help="Session id")],
    enabled: Annotated[
        bool,
        typer.Option("--enabled/--disabled", help="Enable or disable first-use prompts"),
    ] = True,
) -> None:
    """Enable or disable first-use prompts for a session."""
    s = _call(
        "session.set_first_use_prompts",
        {"session_id": session_id, "enabled": enabled},
    )
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
