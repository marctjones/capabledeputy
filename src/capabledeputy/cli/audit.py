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


def _related_provenance(
    events: list[dict[str, Any]],
    *,
    audit_id: str,
    session_id: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for ev in events:
        if session_id is not None and ev.get("session_id") not in {None, session_id}:
            continue
        payload = ev.get("payload") or {}
        if ev.get("event_type") == "provenance.node":
            if payload.get("event_audit_id") == audit_id:
                nodes.append(payload)
        elif (
            ev.get("event_type") == "provenance.edge" and payload.get("event_audit_id") == audit_id
        ):
            edges.append(payload)
    return nodes, edges


@audit_app.callback()
def audit_main(
    ctx: typer.Context,
    event_type: Annotated[
        str | None,
        typer.Option("--type", help="Filter by exact event type, e.g. session.created"),
    ] = None,
    event_type_contains: Annotated[
        str | None,
        typer.Option(
            "--filter",
            help="Filter by event-type substring, e.g. `loop` or `approval`",
        ),
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
    if event_type_contains:
        params["event_type_contains"] = event_type_contains
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


@audit_app.command("explain")
def explain_command(
    audit_id: Annotated[
        str,
        typer.Argument(help="Audit id to explain"),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a human summary"),
    ] = False,
    limit: Annotated[
        int,
        typer.Option(help="Maximum number of recent audit events to scan"),
    ] = 10_000,
) -> None:
    """Explain one audit event from its replayable trace and provenance DAG."""
    events = anyio.run(_client().call, "audit.list", {"limit": limit})["events"]
    target = next((ev for ev in events if ev.get("audit_id") == audit_id), None)
    if target is None:
        console.print(f"[red]audit id not found:[/red] {audit_id}")
        raise typer.Exit(code=1)

    nodes, edges = _related_provenance(
        events,
        audit_id=audit_id,
        session_id=target.get("session_id"),
    )
    payload = target.get("payload") or {}
    explanation = {
        "event": target,
        "policy_trace": payload.get("policy_trace"),
        "provenance_nodes": nodes,
        "provenance_edges": edges,
    }
    if json_output:
        console.print_json(data=explanation)
        return

    console.print(f"[bold]{target['event_type']}[/bold] audit={audit_id}")
    console.print(f"session={target.get('session_id') or '-'}")
    trace = payload.get("policy_trace")
    if trace:
        console.print("[bold]policy trace[/bold]")
        for key in (
            "tool",
            "decision",
            "rule",
            "matched_capability_audit_id",
            "matched_capability_kind",
            "matched_capability_pattern",
            "effect_class",
        ):
            console.print(f"  {key}: {trace.get(key)}")
    elif payload:
        console.print("[bold]payload[/bold]")
        console.print_json(data=payload)
    if nodes or edges:
        console.print("[bold]provenance[/bold]")
        for node in nodes:
            console.print(f"  node {node.get('node_id')} ({node.get('kind')})")
        for edge in edges:
            console.print(
                f"  edge {edge.get('from_node_id')} -> "
                f"{edge.get('to_node_id')} ({edge.get('kind')})",
            )


@audit_app.command("verify")
def verify_command(
    path: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Path to audit.jsonl. Defaults to the daemon's "
                "configured audit log (resolved via daemon.info "
                "when daemon is running, or paths.default_audit_log_path "
                "when not)."
            ),
        ),
    ] = None,
    include_rotated: Annotated[
        bool,
        typer.Option(
            "--include-rotated",
            help=(
                "Walk rotated archives (.1 .2 ...) in chronological "
                "order, threading prev_hash across rotation boundaries. "
                "Slower on large archives but completes the audit-"
                "integrity story for operators using rotation."
            ),
        ),
    ] = False,
) -> None:
    """Walk the audit log's hash chain and report tampering (cookbook P1.6).

    Each event line written by the daemon carries a `prev_hash` field
    equal to the SHA-256 of the prior line's bytes. This command
    re-computes every hash and flags the first line that doesn't
    match — that's where someone edited, inserted, deleted, or
    reordered an event.

    Exit code 0 ⇒ chain verified. Exit code 1 ⇒ tampering detected
    (suitable for scripting / CI guards).
    """
    from pathlib import Path

    from capabledeputy.audit.verify import verify_audit_chain
    from capabledeputy.paths import default_audit_log_path

    resolved: Path
    if path:
        resolved = Path(path)
    else:
        # Prefer the daemon's reported audit_path so this works even
        # when the daemon was started with a non-default override.
        try:
            client = _client()
            info = anyio.run(client.call, "daemon.info")
            audit_path = info.get("audit_path")
            resolved = Path(audit_path) if audit_path else default_audit_log_path()
        except Exception:
            resolved = default_audit_log_path()

    result = verify_audit_chain(resolved, include_rotated=include_rotated)
    if result.ok:
        console.print(
            f"[green]✓ verified[/green]  {result.reason}",
        )
        console.print(f"  path: [dim]{result.path}[/dim]")
        if len(result.files_walked) > 1:
            console.print(
                f"  files walked: [dim]{', '.join(p.name for p in result.files_walked)}[/dim]",
            )
        console.print(
            f"  total lines: {result.n_lines}  "
            f"chained: {result.n_chained}  "
            f"legacy: {result.n_legacy_prefix}",
        )
        raise typer.Exit(code=0)
    console.print(
        f"[red]✗ tampered[/red]  {result.reason}",
    )
    console.print(f"  path: [dim]{result.path}[/dim]")
    if result.tampered_at_file is not None:
        console.print(
            f"  break in file: [bold]{result.tampered_at_file.name}[/bold] "
            f"at line {result.tampered_at_line}",
        )
    else:
        console.print(f"  break at line: {result.tampered_at_line}")
    console.print(
        f"  verified before break: {result.n_chained} chained / {result.n_legacy_prefix} legacy",
    )
    raise typer.Exit(code=1)
