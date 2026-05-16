"""Top-level Typer app for the `capdep` command."""

from __future__ import annotations

from typing import Annotated, Any

import anyio
import typer
from rich.console import Console

from capabledeputy.cli.approval import approval_app
from capabledeputy.cli.audit import audit_app, watch_command
from capabledeputy.cli.chat import chat_command, demo_app
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
app.add_typer(demo_app, name="demo")
app.command("chat")(chat_command)
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
    """Launch the read-only Textual TUI for live monitoring and approvals."""
    from capabledeputy.tui.app import run

    run()


@app.command("console")
def console_command(
    session_id: Annotated[
        str,
        typer.Argument(help="Session id to drive (see `capdep session list`)"),
    ],
) -> None:
    """Unified TUI: drive the agent, monitor the live security state,
    and grant approvals — one window, no second terminal."""
    client = DaemonClient(default_socket_path())
    try:
        anyio.run(client.call, "ping", {})
    except DaemonNotRunningError:
        err_console.print(
            "[red]daemon not running.[/red] start it with "
            "[bold]capdep daemon start[/bold] and retry.",
        )
        raise typer.Exit(code=2) from None
    from capabledeputy.tui.console import run

    run(session_id)


@app.command("dry-run")
def dry_run_command(
    program: str = typer.Argument(..., help="Path to a programmatic-mode .py source file"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of rendered output"),
) -> None:
    """Statically analyze a programmatic-mode source against the policy.

    Parses + symbolically executes the program; reports every predicted
    tool call with its predicted policy decision. No tool handlers run
    and no session state mutates.
    """
    import json as _json
    from pathlib import Path

    source = Path(program).read_text(encoding="utf-8")
    client = DaemonClient(default_socket_path())
    result = anyio.run(client.call, "programmatic.dry_run", {"source": source})

    if json_output:
        console.print(_json.dumps(result, indent=2))
        raise typer.Exit(code=0 if result["ok"] else 1)

    if result.get("parse_error"):
        err_console.print(f"[red]parse error:[/red] {result['parse_error']}")
        raise typer.Exit(code=2)

    for call in result["tool_calls"]:
        decision = call["decision"]
        color = {"allow": "green", "deny": "red", "require_approval": "yellow"}.get(
            decision,
            "white",
        )
        line = f"line {call['line']}" if call["line"] is not None else "?"
        labels = (
            f" labels={','.join(call['arg_labels'])}" if call["arg_labels"] else ""
        )
        rule = f" rule={call['rule']}" if call["rule"] else ""
        console.print(
            f"  [{color}]{decision}[/{color}] {call['tool']}({line}){labels}{rule}",
        )

    if result["violations"]:
        n = len(result["violations"])
        err_console.print(f"[red]{n} violation(s) — program would not execute[/red]")
        raise typer.Exit(code=1)
    if result.get("runtime_error"):
        err_console.print(f"[red]runtime error:[/red] {result['runtime_error']}")
        raise typer.Exit(code=3)
    console.print(f"[green]ok[/green] — {len(result['tool_calls'])} call(s) predicted")


@app.command("run")
def run_command(
    session_id: str = typer.Argument(..., help="Session id to run inside"),
    program: str = typer.Argument(..., help="Path to a programmatic-mode .py source file"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of rendered output"),
    bundle: bool = typer.Option(
        False,
        "--bundle",
        help=(
            "Bundled-approval mode: dry-run the program; show the impact tree; "
            "prompt for one approval; then execute with each gate pre-applied "
            "via a purpose-limited session."
        ),
    ),
    auto_approve: bool = typer.Option(
        False,
        "--auto-approve",
        help="(With --bundle) skip the prompt; approve every gate. CI-friendly.",
    ),
) -> None:
    """Execute a programmatic-mode source against a session.

    Default mode: each `call(...)` dispatches through `LabeledToolClient`;
    a non-ALLOW decision halts the program with the rule recorded.

    Bundle mode (`--bundle`): one human decision authorises every
    approval gate in the workflow. Useful when a workflow needs many
    related approvals — review the whole plan once, approve, execute.
    """
    if bundle:
        _run_bundled(session_id, program, json_output=json_output, auto_approve=auto_approve)
        return
    import json as _json
    from pathlib import Path

    source = Path(program).read_text(encoding="utf-8")
    client = DaemonClient(default_socket_path())
    result = anyio.run(
        client.call,
        "programmatic.run",
        {"source": source, "session_id": session_id},
    )

    if json_output:
        console.print(_json.dumps(result, indent=2))
        raise typer.Exit(code=0 if result["ok"] else 1)

    if result.get("parse_error"):
        err_console.print(f"[red]parse error:[/red] {result['parse_error']}")
        raise typer.Exit(code=2)

    for call in result["tool_calls"]:
        decision = call["decision"]
        color = {"allow": "green", "deny": "red", "require_approval": "yellow"}.get(
            decision,
            "white",
        )
        rule = f" rule={call['rule']}" if call["rule"] else ""
        console.print(f"  [{color}]{decision}[/{color}] {call['tool']}{rule}")

    if result.get("error"):
        err_console.print(f"[red]halted:[/red] {result['error']}")
        raise typer.Exit(code=1)

    if result.get("return_value"):
        rv = result["return_value"]
        labels = ",".join(rv["labels"]) or "-"
        console.print(f"[bold]return:[/bold] {rv['raw']!r} [dim](labels={labels})[/dim]")
    console.print(f"[green]ok[/green] — {len(result['tool_calls'])} call(s) executed")


def _run_bundled(
    session_id: str,
    program: str,
    *,
    json_output: bool,
    auto_approve: bool,
) -> None:
    """Bundled-approval execution path: dry-run → preview → approve → execute."""
    import json as _json
    from pathlib import Path

    source = Path(program).read_text(encoding="utf-8")
    client = DaemonClient(default_socket_path())

    preview = anyio.run(client.call, "programmatic.bundle_dry_run", {"source": source})

    if json_output and not auto_approve:
        # Dry-run only when --json without --auto-approve.
        console.print(_json.dumps(preview, indent=2))
        raise typer.Exit(code=0 if preview["is_approvable"] else 1)

    console.print(preview["rendered"])

    if not preview["is_approvable"]:
        err_console.print(
            "[red]bundle has non-negotiable DENY(s); execution refused.[/red]",
        )
        raise typer.Exit(code=1)

    if not auto_approve:
        try:
            answer = typer.prompt("Approve and execute the bundle? [y/N]", default="N")
        except typer.Abort:
            answer = "N"
        if answer.strip().lower() not in ("y", "yes"):
            console.print("[yellow]bundle declined; nothing executed.[/yellow]")
            raise typer.Exit(code=2)

    # Mark every PENDING gate APPROVED before submitting for execution.
    impact = preview["impact"]
    impact["gates"] = [
        {**g, "state": "approved" if g["state"] == "pending" else g["state"]}
        for g in impact["gates"]
    ]
    result = anyio.run(
        client.call,
        "programmatic.bundle_execute",
        {"source": source, "session_id": session_id, "impact": impact},
    )
    if json_output:
        console.print(_json.dumps(result, indent=2))
        raise typer.Exit(code=0 if result["ok"] else 1)
    if not result["ok"]:
        err_console.print(f"[red]halted:[/red] {result['error']}")
        raise typer.Exit(code=1)
    console.print(f"[green]ok[/green] — {result['n_steps']} step(s) executed via bundle")


@app.command("send")
def send_message(
    session_id: str = typer.Argument(..., help="Session id"),
    message: str = typer.Argument(..., help="User message to send"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of rendered output"),
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="Force the execution mode for this turn (turn_level, dual_llm, programmatic).",
    ),
) -> None:
    """Send a user message to a session and run one agent turn."""
    import json as _json

    client = DaemonClient(default_socket_path())
    params: dict[str, Any] = {"session_id": session_id, "message": message}
    if mode:
        params["mode"] = mode
    result = anyio.run(client.call, "session.send", params)

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
def daemon_start(
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help=(
                "Log every RPC request to stderr with timing and a "
                "short summary. Cache-y methods are dimmed; slow calls "
                "(>500ms) are highlighted."
            ),
        ),
    ] = False,
    no_policy_preview: Annotated[
        bool,
        typer.Option(
            "--no-policy-preview",
            help=(
                "Do not register the read-only policy.preview tool. "
                "Enforcement is unaffected (decide() runs at dispatch "
                "regardless). Disabling it makes the agent's "
                "policy-probing show up as loud audited denied calls "
                "instead of silent queries, and keeps the agent's "
                "capability surface strictly minimal. Default: enabled "
                "(better agent planning). Overrides CAPDEP_POLICY_PREVIEW."
            ),
        ),
    ] = False,
) -> None:
    """Start the daemon in the foreground. Blocks until shutdown."""
    console.print("[green]capdep daemon starting[/green]")
    if verbose:
        console.print("[dim]verbose RPC logging enabled[/dim]")
    if no_policy_preview:
        console.print("[dim]policy.preview tool disabled[/dim]")
    try:
        # None → run_daemon falls through to CAPDEP_POLICY_PREVIEW / default.
        # False → hard-disable (CLI flag wins over env).
        anyio.run(
            lambda: run_daemon(
                verbose=verbose,
                policy_preview=False if no_policy_preview else None,
            ),
        )
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
