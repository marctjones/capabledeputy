"""Top-level Typer app for the `capdep` command."""

from __future__ import annotations

from typing import Annotated, Any

import anyio
import typer
from rich.console import Console

from capabledeputy.cli.approval import approval_app
from capabledeputy.cli.audit import audit_app, watch_command
from capabledeputy.cli.audit_cmd import storage_shape_command
from capabledeputy.cli.chat import chat_command, demo_app
from capabledeputy.cli.init_cmd import init_command
from capabledeputy.cli.maintenance import maintenance_app
from capabledeputy.cli.override_cmd import override_app
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
app.add_typer(override_app, name="override")
app.add_typer(demo_app, name="demo")
app.add_typer(maintenance_app, name="maintenance")
config_app = typer.Typer(help="Manage CapableDeputy config files.", no_args_is_help=True)
app.add_typer(config_app, name="config")
oauth_app = typer.Typer(
    help="Manage native OAuth tokens for remote MCP servers.",
    no_args_is_help=True,
)
app.add_typer(oauth_app, name="oauth")
app.command("chat")(chat_command)
app.command("init")(init_command)
app.command("watch")(watch_command)
audit_app.command("storage-shape")(storage_shape_command)


@oauth_app.command("login")
def oauth_login_command(
    server: Annotated[
        str,
        typer.Option("--server", "-s", help="Upstream server name in the config."),
    ],
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            "-c",
            help="Daemon/curated config to read (default: user daemon config).",
        ),
    ] = None,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Print the authorization URL without opening it."),
    ] = False,
    timeout: Annotated[
        int,
        typer.Option("--timeout", help="Seconds to wait for the local OAuth callback."),
    ] = 180,
) -> None:
    """Run a native OAuth2 browser login for a remote MCP upstream."""
    from pathlib import Path

    from capabledeputy.cli._managed_config import user_default_daemon_config_path
    from capabledeputy.upstream.config import load_config_file
    from capabledeputy.upstream.http_auth import perform_oauth2_login

    config_path = Path(config).expanduser() if config else user_default_daemon_config_path()
    if not config_path.is_file():
        err_console.print(f"[red]config not found:[/red] {config_path}")
        raise typer.Exit(code=2)
    servers = load_config_file(config_path)
    match = next((candidate for candidate in servers if candidate.name == server), None)
    if match is None:
        known = ", ".join(sorted(candidate.name for candidate in servers))
        err_console.print(f"[red]server not found:[/red] {server}. Known servers: {known}")
        raise typer.Exit(code=2)
    if match.auth is None or match.auth.type != "oauth2":
        err_console.print(
            f"[red]{server} is not configured with auth.type oauth2[/red]",
        )
        raise typer.Exit(code=2)

    token_path = perform_oauth2_login(
        match.auth,
        server_name=match.name,
        open_browser=not no_browser,
        timeout_seconds=timeout,
        emit=console.print,
    )
    console.print(f"[green]stored OAuth token:[/green] {token_path}")


@config_app.command("doctor")
def config_doctor_command(
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            "-c",
            help="Daemon config to inspect (default: ~/.config/capabledeputy/daemon.yaml)",
        ),
    ] = None,
) -> None:
    """Verify the daemon config is wired correctly for the granular
    permission kinds (Issue #33, #35).

    Checks:
    - daemon.yaml or servers.d/*.yaml present and parseable
    - Gmail read/draft tools use GMAIL_READ / GMAIL_DRAFT (not legacy READ_FS)
    - IMAP tools use IMAP_READ
    - Drive tools use DRIVE_READ where appropriate
    - servers.d/ files validate (namespace, no collisions)
    - Default auto-grant set covers GMAIL_READ / IMAP_READ / DRIVE_READ

    Prints a status report; exits 0 if everything checks out, 1
    otherwise. Operator runs this after `capdep gworkspace-setup`
    / `capdep imap-setup` to confirm the wiring is current.
    """
    from pathlib import Path

    import yaml
    from rich.console import Console

    console = Console()
    src_path = Path(config or Path.home() / ".config" / "capabledeputy" / "daemon.yaml")
    issues: list[str] = []
    ok: list[str] = []
    notes: list[str] = []

    # 1. daemon.yaml exists + parses
    if not src_path.is_file():
        console.print(f"[red]✗[/red] daemon.yaml not found at {src_path}")
        console.print(
            "[dim]  Run [bold]capdep gworkspace-setup[/bold] / [bold]capdep imap-setup[/bold] "
            "to create one, or specify --config.[/dim]",
        )
        raise typer.Exit(code=1)
    ok.append(f"daemon.yaml found at {src_path}")

    try:
        raw = yaml.safe_load(src_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        console.print(f"[red]✗[/red] daemon.yaml invalid YAML: {e}")
        raise typer.Exit(code=1) from None
    ok.append("daemon.yaml parses cleanly")

    # 2. Check upstream_servers' Gmail tools use granular Gmail kinds
    servers = raw.get("upstream_servers") or []
    for server in servers:
        name = server.get("name", "?")
        overrides = server.get("tool_overrides") or {}
        for tool_name, ov in overrides.items():
            kind = ov.get("capability_kind") if isinstance(ov, dict) else None
            if not kind:
                continue
            # Gmail tools should be GMAIL_READ, GMAIL_DRAFT, SEND_EMAIL,
            # or explicit mutation kinds.
            tl = tool_name.lower()
            if "gmail" in tl and kind == "READ_FS":
                issues.append(
                    f"server '{name}' tool '{tool_name}': uses legacy READ_FS; "
                    f"should be GMAIL_READ. Re-run capdep gworkspace-setup to update."
                )
            elif "imap" in tl and kind == "READ_FS":
                issues.append(
                    f"server '{name}' tool '{tool_name}': uses legacy READ_FS; "
                    f"should be IMAP_READ. Re-run capdep imap-setup to update."
                )
            elif tl.startswith("drive_") and "list" in tl and kind == "READ_FS":
                notes.append(
                    f"server '{name}' tool '{tool_name}': READ_FS works via "
                    f"back-compat union; consider DRIVE_READ for clarity."
                )

    if servers:
        ok.append(f"{len(servers)} upstream server(s) declared in daemon.yaml")
    else:
        notes.append("no upstream_servers: block in daemon.yaml (only bundled tools)")

    # 3. Check servers.d/ if present
    servers_d = src_path.parent / "servers.d"
    if servers_d.is_dir():
        from capabledeputy.upstream.server_yaml import (
            KindCollisionError,
            UnknownOverrideTargetError,
            load_servers_d,
        )

        try:
            yamls, overrides, registry = load_servers_d(servers_d)
            ok.append(
                f"servers.d/ has {len(yamls)} server file(s), "
                f"{len(overrides)} override(s), "
                f"{len(registry.all())} custom kind(s)",
            )
        except (KindCollisionError, UnknownOverrideTargetError) as e:
            issues.append(f"servers.d/ load error: {e}")
    else:
        notes.append("no servers.d/ directory (legacy daemon.yaml layout only)")

    # 4. Check upstream adapter inference — sanity-check that
    # gmail tool names classify correctly
    from capabledeputy.policy.capabilities import CapabilityKind
    from capabledeputy.upstream.adapter import _infer_capability_kind

    test_cases = [
        ("gmail.users.messages.list", CapabilityKind.GMAIL_READ),
        ("gmail_messages_get", CapabilityKind.GMAIL_READ),
        ("gmail.users.drafts.create", CapabilityKind.GMAIL_DRAFT),
        ("imap.fetch", CapabilityKind.IMAP_READ),
        ("drive.files.list", CapabilityKind.DRIVE_READ),
        ("gmail.users.messages.send", CapabilityKind.SEND_EMAIL),
        ("chat.search_messages", CapabilityKind.CHAT_READ),
        ("chat.send_message", CapabilityKind.SEND_MESSAGE),
        ("people.search_contacts", CapabilityKind.PEOPLE_READ),
    ]
    classifier_issues = []
    for tool_name, expected in test_cases:
        got = _infer_capability_kind(None, tool_name)
        if got != expected:
            classifier_issues.append(f"{tool_name} → {got} (expected {expected.value})")
    if classifier_issues:
        issues.append(
            "Upstream classifier regression: " + "; ".join(classifier_issues),
        )
    else:
        ok.append("upstream classifier correctly maps gmail/imap/drive tools")

    # 5. Report
    console.print()
    console.print("[bold]capdep config doctor[/bold]")
    console.print()
    for line in ok:
        console.print(f"  [green]✓[/green] {line}")
    for line in notes:
        console.print(f"  [dim]·[/dim] {line}")
    for line in issues:
        console.print(f"  [red]✗[/red] {line}")
    console.print()
    if issues:
        console.print(f"[red]{len(issues)} issue(s) found[/red] — fix as suggested above.")
        raise typer.Exit(code=1)
    console.print("[green]All checks passed.[/green]")


@config_app.command("split")
def config_split_command(
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            "-c",
            help="Source daemon.yaml (default: ~/.config/capabledeputy/daemon.yaml)",
        ),
    ] = None,
    output_dir: Annotated[
        str | None,
        typer.Option(
            "--output-dir",
            "-o",
            help=(
                "Target servers.d/ directory (default: alongside the source "
                "config). Files written: <name>.yaml per upstream server. "
                "Dry-run prints what would be written."
            ),
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print extracted yaml to stdout instead of writing files.",
        ),
    ] = False,
) -> None:
    """Migrate a legacy daemon.yaml `servers:` block into per-server
    files in `servers.d/` (Issue #35).

    For each server entry in the source `upstream_servers` list, writes
    a `servers.d/<name>.yaml` containing connection details + isolation
    + per-tool overrides. The new file uses schema_version: 1 and the
    short-form `tool_mappings` syntax where possible.

    The source file itself is updated: the `upstream_servers` block is
    commented out (preserved for rollback) and a `servers_dir:`
    reference added.
    """
    from pathlib import Path

    import yaml

    src_path = Path(config or Path.home() / ".config" / "capabledeputy" / "daemon.yaml")
    if not src_path.is_file():
        err_console.print(f"[red]source config not found:[/red] {src_path}")
        raise typer.Exit(code=2)

    src_text = src_path.read_text(encoding="utf-8")
    src_raw = yaml.safe_load(src_text) or {}
    servers_raw = src_raw.get("upstream_servers") or []
    if not servers_raw:
        err_console.print(
            f"[yellow]no `upstream_servers:` block found in {src_path}[/yellow] "
            "— nothing to split. If you already have a servers.d/ layout, "
            "this command isn't needed.",
        )
        raise typer.Exit(code=0)

    target_dir = Path(output_dir or src_path.parent / "servers.d")
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for entry in servers_raw:
        name = str(entry.get("name") or "").strip()
        if not name:
            err_console.print("[yellow]skipping unnamed server entry[/yellow]")
            continue
        # Shape into new schema
        out_doc: dict[str, Any] = {"schema_version": 1, "name": name}
        if "command" in entry:
            out_doc["command"] = list(entry["command"])
        if entry.get("transport"):
            out_doc["transport"] = entry["transport"]
        if entry.get("url"):
            out_doc["url"] = entry["url"]
        if entry.get("server_url"):
            out_doc["server_url"] = entry["server_url"]
        if entry.get("http_url"):
            out_doc["http_url"] = entry["http_url"]
        if entry.get("headers"):
            out_doc["headers"] = entry["headers"]
        if entry.get("auth"):
            out_doc["auth"] = entry["auth"]
        if entry.get("env"):
            out_doc["env"] = entry["env"]
        if entry.get("inherent_labels"):
            out_doc["inherent_labels"] = entry["inherent_labels"]
        if entry.get("inherent_tags"):
            out_doc["inherent_tags"] = entry["inherent_tags"]
        if entry.get("strict") is not None:
            out_doc["strict"] = bool(entry["strict"])
        if entry.get("isolation"):
            out_doc["isolation"] = entry["isolation"]

        # Convert tool_overrides → short form tool_mappings where every
        # entry is just `{capability_kind: <K>}` with no additional labels.
        overrides = entry.get("tool_overrides") or {}
        mappings: dict[str, str] = {}
        complex_overrides: dict[str, dict[str, Any]] = {}
        for tool_name, ov in overrides.items():
            if isinstance(ov, dict) and len(ov) == 1 and "capability_kind" in ov:
                mappings[str(tool_name)] = str(ov["capability_kind"])
            else:
                complex_overrides[str(tool_name)] = ov
        if mappings:
            out_doc["tool_mappings"] = mappings
        if complex_overrides:
            out_doc["tool_overrides"] = complex_overrides

        out_yaml = yaml.safe_dump(out_doc, sort_keys=False, default_flow_style=False)

        if dry_run:
            console = _make_console()
            console.print(
                f"[bold cyan]--- would write: {target_dir / f'{name}.yaml'} ---[/bold cyan]"
            )
            console.print(out_yaml)
        else:
            target_file = target_dir / f"{name}.yaml"
            target_file.write_text(out_yaml, encoding="utf-8")
            written.append(str(target_file))

    if dry_run:
        err_console.print(f"[dim](dry-run; would write {len(servers_raw)} files)[/dim]")
        return

    # Mutate source: comment out the upstream_servers block.
    # Conservative approach — line-based replacement that preserves
    # surrounding YAML structure. The user can recover by uncommenting
    # the original block.
    new_lines: list[str] = []
    in_block = False
    block_indent = -1
    for line in src_text.splitlines():
        stripped = line.lstrip()
        cur_indent = len(line) - len(stripped)
        if stripped.startswith("upstream_servers:") and not in_block:
            in_block = True
            block_indent = cur_indent
            new_lines.append(f"# MIGRATED to servers.d/ on {_now_iso()} via `capdep config split`")
            new_lines.append(f"# {line}")
            continue
        if in_block:
            # In-block until we hit a line at <= block_indent that's not blank
            if stripped and cur_indent <= block_indent:
                in_block = False
                # Add servers_dir reference at this insertion point
                new_lines.append("servers_dir: ./servers.d/")
                new_lines.append("")
                new_lines.append(line)
                continue
            new_lines.append(f"# {line}")
        else:
            new_lines.append(line)
    # If block ran to EOF
    if in_block:
        new_lines.append("servers_dir: ./servers.d/")

    src_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    err_console.print(
        f"[green]✓ wrote {len(written)} server file(s) to {target_dir}[/green]",
    )
    err_console.print(
        "[dim]source updated: legacy `upstream_servers:` block commented out, "
        "`servers_dir: ./servers.d/` added.[/dim]",
    )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def _make_console():
    from rich.console import Console

    return Console()


@app.command("go")
def go_command(
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            "-c",
            help=(
                "Daemon config (upstream MCP servers). Default: bundled "
                "Python servers only. Personal-assistant preset: "
                "configs/personal-assistant/daemon.yaml"
            ),
        ),
    ] = None,
    intent: Annotated[
        str | None,
        typer.Option("--intent", help="Intent for the auto-created session"),
    ] = None,
) -> None:
    """One-shot: ensure daemon is running, create a session, enter chat.

    Replaces the manual three-step ritual:
      1. capdep daemon start ...   (in one terminal)
      2. capdep session new        (in another)
      3. capdep chat <id>          (with the id from step 2)

    With a single command. If the daemon is already running it's
    reused; otherwise it's started in the background. A fresh
    session is created and you drop straight into the REPL.
    """
    import subprocess as _subprocess
    import time as _time
    from pathlib import Path as _Path

    # Check daemon — start if not running
    try:
        anyio.run(DaemonClient(default_socket_path()).call, "ping", {})
        console.print("[dim]daemon already running[/dim]")
    except DaemonNotRunningError:
        console.print("[green]starting daemon in background...[/green]")
        cmd = [sys.executable, "-m", "capabledeputy.cli.main", "daemon", "start"]
        if config:
            cmd.extend(["--config", config])
        # Background spawn; log to stderr file so the user can debug
        log_path = _Path("/tmp") / f"capdep-daemon-{int(_time.time())}.log"
        log_file = log_path.open("w")
        _subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
        console.print(f"[dim]daemon log: {log_path}[/dim]")
        # Poll for socket up to 10s
        for _ in range(50):
            _time.sleep(0.2)
            try:
                anyio.run(DaemonClient(default_socket_path()).call, "ping", {})
                console.print("[green]daemon ready[/green]")
                break
            except DaemonNotRunningError:
                continue
        else:
            err_console.print(
                f"[red]daemon failed to start within 10s; check {log_path}[/red]",
            )
            raise typer.Exit(code=2)

    # Create session + drop into chat
    chat_command(session_id=None, intent=intent, new=False)


import sys  # noqa: E402 (late import inside command body)


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


@app.command("why")
def why_command(
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Filter to one session id"),
    ] = None,
    tool: Annotated[
        str | None,
        typer.Option("--tool", help="Filter to one tool name"),
    ] = None,
    last: Annotated[
        int,
        typer.Option(help="How many recent decisions to explain"),
    ] = 1,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Explain a policy decision (#49): the rule / floor / inspector that
    fired. Reads the audit log and, for each recent `policy.decided`, shows
    the base rule + reason, the v2 outcome + matched rule ids, and any
    decision-inspector adjustment or relaxation refusal that shaped it."""
    import json as _json

    client = DaemonClient(default_socket_path())
    params: dict[str, object] = {"limit": 2000}
    if session_id:
        params["session_id"] = session_id
    events = anyio.run(client.call, "audit.list", params)["events"]

    decided = [e for e in events if e.get("event_type") == "policy.decided"]
    if tool:
        decided = [e for e in decided if e.get("payload", {}).get("tool") == tool]
    if not decided:
        err_console.print("[yellow]no matching policy decisions in the audit log[/yellow]")
        raise typer.Exit(code=1)

    explanations = [_explain_decision(d, events) for d in decided[-last:]]

    if json_output:
        console.print(_json.dumps(explanations, indent=2))
        return

    for ex in explanations:
        color = {"allow": "green", "deny": "red", "require_approval": "yellow"}.get(
            ex["decision"],
            "white",
        )
        console.print(
            f"\n[bold]{ex['tool']}[/bold] → [{color}]{ex['decision']}[/{color}]",
        )
        if ex.get("rule"):
            console.print(f"  rule: {ex['rule']}")
        if ex.get("reason"):
            console.print(f"  reason: {ex['reason']}")
        if ex.get("v2_outcome"):
            console.print(f"  v2 outcome: {ex['v2_outcome']}")
        if ex.get("v2_matched_rule_ids"):
            console.print(f"  matched rules: {', '.join(ex['v2_matched_rule_ids'])}")
        adj = ex.get("inspector_adjustment")
        if adj:
            console.print(
                f"  [cyan]inspector:[/cyan] {adj['applied_rule']} "
                f"({adj['original_decision']} → {adj['adjusted_decision']})"
                + (f" — {adj['rationale']}" if adj.get("rationale") else ""),
            )
        if ex.get("relaxation_refused"):
            console.print("  [red]relaxation refused (FR-031 asymmetry)[/red]")


def _explain_decision(
    decided: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the explanation for one `policy.decided`, correlating the
    nearest preceding decision-inspector adjustment for the same tool."""
    payload = decided.get("payload", {})
    tool_name = payload.get("tool")
    # Identity-based locate — robust to two decisions with equal payloads.
    idx = next((i for i, e in enumerate(events) if e is decided), len(events))
    inspector_adjustment = None
    for prior in reversed(events[:idx]):
        et = prior.get("event_type")
        if et in ("policy.decided",):
            break  # don't cross into the previous decision
        if et == "decision_inspector.applied" and prior.get("payload", {}).get("tool") == tool_name:
            inspector_adjustment = prior.get("payload")
            break
    return {
        "tool": tool_name,
        "decision": payload.get("decision", "?"),
        "rule": payload.get("rule"),
        "reason": payload.get("reason"),
        "v2_outcome": payload.get("v2_outcome"),
        "v2_matched_rule_ids": payload.get("v2_matched_rule_ids", []),
        "inspector_adjustment": inspector_adjustment,
        "relaxation_refused": bool(payload.get("refused_relax_inputs")),
        "timestamp": decided.get("timestamp"),
    }


console = Console()
err_console = Console(stderr=True)


@app.command("status")
def app_status_command(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show daemon-owned application status for all client surfaces."""
    result = anyio.run(DaemonClient(default_socket_path()).call, "app.status", {})
    if json_output:
        console.print_json(data=result)
        return
    daemon = result.get("daemon", {})
    model = result.get("model", {})
    console.print(f"[bold]capdep[/bold] {result.get('version', '')}")
    console.print(f"daemon connected: {daemon.get('connected', False)}")
    console.print(f"sessions: {daemon.get('session_count', 0)}")
    console.print(f"active sessions: {daemon.get('active_session_count', 0)}")
    console.print(f"pending approvals: {daemon.get('pending_approval_count', 0)}")
    console.print(f"tools: {daemon.get('tool_count', 0)}")
    console.print(f"planner model: {model.get('planner', '') or '(none)'}")
    console.print(f"local model available: {model.get('local_available', False)}")


@app.command("setup-status")
def setup_status_command(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show daemon-owned setup checks and remediation hints."""
    result = anyio.run(DaemonClient(default_socket_path()).call, "setup.status", {})
    if json_output:
        console.print_json(data=result)
        return
    from rich.table import Table

    checks = result.get("checks", [])
    table = Table(title=f"Setup checks ({len(checks)})")
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Detail")
    table.add_column("Actions")
    for check in checks:
        actions = ", ".join(action.get("label", "") for action in check.get("actions", []))
        table.add_row(
            check.get("id", ""),
            check.get("status", ""),
            check.get("detail", ""),
            actions,
        )
    console.print(table)


@app.command("memory")
def memory_command(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List daemon memory entries and labels."""
    result = anyio.run(DaemonClient(default_socket_path()).call, "memory.entries", {})
    if json_output:
        console.print_json(data=result)
        return
    from rich.table import Table

    entries = result.get("entries", [])
    table = Table(title=f"Memory entries ({len(entries)})")
    table.add_column("Key")
    table.add_column("Labels")
    for entry in entries:
        table.add_row(entry.get("key", ""), ", ".join(entry.get("labels", [])))
    console.print(table)


@app.command("provenance")
def provenance_command(
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Filter to one session id"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show the daemon materialized provenance graph."""
    params: dict[str, Any] = {}
    if session_id:
        params["session_id"] = session_id
    result = anyio.run(DaemonClient(default_socket_path()).call, "provenance.graph", params)
    if json_output:
        console.print_json(data=result)
        return
    console.print(
        f"nodes: {len(result.get('nodes', []))}  edges: {len(result.get('edges', []))}",
    )
    for node in result.get("nodes", [])[:25]:
        console.print(f"  node {node.get('id', '')} [{node.get('kind', '')}]")
    for edge in result.get("edges", [])[:25]:
        console.print(
            f"  edge {edge.get('from', '')} -> {edge.get('to', '')} [{edge.get('kind', '')}]",
        )


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
    """[DEPRECATED] Launch the read-only Textual TUI.

    The read-only-spectator role is being folded into `capdep chat --mode rich`
    (Issue #15 Phase B already lands the dispatch). When the rich surface
    reaches feature parity, this command will be removed. Use
    `capdep chat --mode rich` for new workflows.
    """
    from capabledeputy.tui.app import run

    err_console.print(
        "[yellow]warning:[/yellow] [bold]capdep tui[/bold] is deprecated. "
        "Use [bold]capdep chat --mode rich[/bold] on a modern terminal "
        "(Ghostty / kitty / iTerm2 / WezTerm / Alacritty) for the same "
        "Textual surface with an active input box — full convergence per "
        "Issue #15 / spec 007. This command will be removed once the rich "
        "surface reaches feature parity.",
    )
    run()


@app.command("console")
def console_command(
    session_id: Annotated[
        str,
        typer.Argument(help="Session id to drive (see `capdep session list`)"),
    ],
) -> None:
    """[DEPRECATED] Unified TUI to drive + monitor.

    Folded into `capdep chat --mode rich` (Issue #15 Phase B). The rich
    surface auto-detects modern terminals and falls back to line mode
    elsewhere. When feature parity is reached, this command will be
    removed.
    """
    err_console.print(
        "[yellow]warning:[/yellow] [bold]capdep console[/bold] is deprecated. "
        "Use [bold]capdep chat --mode rich[/bold] (the rich surface scaffold "
        "calls into the same Textual app per Issue #15 Phase B). The "
        "convergence is in progress; this command will be removed once "
        "the rich surface reaches feature parity.",
    )
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


@app.command("ui")
def ui_command(
    demo: Annotated[
        bool,
        typer.Option(
            "--demo/--no-demo",
            help="Run the scripted showcase (no daemon needed).",
        ),
    ] = True,
) -> None:
    """Launch the inline console (greenfield TUI redesign).

    An inline, streaming, conversational REPL: a fixed engine-sourced status
    line, decision cards drawn from typed engine decisions (never model prose),
    quarantine-rendered untrusted content, an armed approval interaction, and a
    ctrl+k kill switch. `--demo` runs a scripted end-to-end showcase; live
    daemon wiring is in progress.
    """
    from capabledeputy.tui.inline.app import InlineConsole
    from capabledeputy.tui.inline.demo import DemoDriver
    from capabledeputy.tui.inline.status import TrustState

    if not demo:
        err_console.print(
            "[yellow]live daemon wiring is in progress; running --demo.[/yellow]",
        )
    InlineConsole(
        DemoDriver(),
        trust=TrustState(
            session_name="morning-triage",
            purpose="daily-life",
            clearance="restricted",
        ),
    ).run(inline=True)


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
        labels = f" labels={','.join(call['arg_labels'])}" if call["arg_labels"] else ""
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
        label_payload = rv.get("labels") or {}
        axis_a = ",".join(label_payload.get("axis_a", [])) or "-"
        axis_b = ",".join(label_payload.get("axis_b", [])) or "-"
        if rv.get("redacted"):
            console.print(
                "[bold]return:[/bold] <redacted labeled value> "
                f"[dim](axis_a={axis_a}; axis_b={axis_b})[/dim]",
            )
        else:
            console.print(
                f"[bold]return:[/bold] {rv.get('raw')!r} "
                f"[dim](axis_a={axis_a}; axis_b={axis_b})[/dim]",
            )
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
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            "-c",
            help=(
                "Path to a daemon config file with an `upstream_servers:` "
                "section (e.g. configs/curated/official-reference.yaml). "
                "Those MCP servers are spawned for the daemon's lifetime "
                "and their tools registered behind the policy engine, so "
                "chat/console/tui see them automatically. Opt-in: omitted "
                "= native tools only. Overrides CAPDEP_CONFIG."
            ),
        ),
    ] = None,
) -> None:
    """Start the daemon in the foreground. Blocks until shutdown."""
    from pathlib import Path

    console.print("[green]capdep daemon starting[/green]")
    if verbose:
        console.print("[dim]verbose RPC logging enabled[/dim]")
    if no_policy_preview:
        console.print("[dim]policy.preview tool disabled[/dim]")
    if config:
        console.print(f"[dim]daemon config: {config}[/dim]")
    try:
        # None → run_daemon falls through to CAPDEP_POLICY_PREVIEW / default.
        # False → hard-disable (CLI flag wins over env).
        anyio.run(
            lambda: run_daemon(
                verbose=verbose,
                policy_preview=False if no_policy_preview else None,
                config_path=Path(config) if config else None,
            ),
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]capdep daemon stopped (SIGINT)[/yellow]")


def _make_bundled_mcp_command(module_name: str):
    """Factory: returns a CLI handler that runs the named bundled
    MCP server. Used by `capdep mcp-server-<name>` commands that expose
    CapableDeputy's own Python MCP servers via stdio.

    These are STANDALONE servers — no CapableDeputy daemon required.
    Within CapableDeputy: include them in an upstream_servers config
    to load them as curated upstream tool sources.
    """

    def _handler() -> None:
        import importlib

        module = importlib.import_module(f"capabledeputy.mcp_servers.{module_name}")
        module.main()

    _handler.__name__ = f"mcp_server_{module_name}_command"
    _handler.__doc__ = (
        f"Run the bundled `{module_name}` MCP server over stdio. "
        f"Configure your MCP host (Claude Desktop, etc.) to launch "
        f"this command, or include it in a CapableDeputy "
        f"upstream_servers config."
    )
    return _handler


# Bundled Python MCP servers (minimal-install story; no Node.js / npm).
# Each can be launched standalone OR included in an upstream_servers
# config so the daemon spawns it as a curated upstream tool source.
app.command("mcp-server-fs")(_make_bundled_mcp_command("fs"))
app.command("mcp-server-fetch")(_make_bundled_mcp_command("fetch"))
app.command("mcp-server-search")(_make_bundled_mcp_command("search"))
app.command("mcp-server-memory")(_make_bundled_mcp_command("memory"))
app.command("mcp-server-git")(_make_bundled_mcp_command("git"))
app.command("mcp-server-imap")(_make_bundled_mcp_command("imap"))
app.command("mcp-server-applescript")(_make_bundled_mcp_command("applescript"))
app.command("mcp-server-apple-mail")(_make_bundled_mcp_command("apple_mail"))
app.command("mcp-server-keynote")(_make_bundled_mcp_command("keynote"))
app.command("mcp-server-pages")(_make_bundled_mcp_command("pages"))
app.command("mcp-server-numbers")(_make_bundled_mcp_command("numbers"))
app.command("mcp-server-macos")(_make_bundled_mcp_command("macos"))


@app.command("setup")
def setup_command(
    no_sandbox: Annotated[
        bool,
        typer.Option(
            "--no-sandbox",
            help="Don't register a Podman sandbox region even if podman is detected.",
        ),
    ] = False,
    force_sandbox: Annotated[
        bool,
        typer.Option(
            "--force-sandbox",
            help="Register the sandbox block even if podman isn't on PATH (you'll install it later).",  # noqa: E501
        ),
    ] = False,
) -> None:
    """Register the standard bundled-server assistant surface — fs,
    memory, git, fetch, search — plus a Podman sandbox region if
    available.

    Writes managed blocks under `~/.config/capabledeputy/daemon.yaml`.
    Re-running is safe and idempotent: existing blocks are refreshed,
    user-authored content between managed markers is preserved.

    Use this instead of `imap-setup` when you don't want Gmail wired
    up. Run both if you want both — they update different managed
    blocks in the same file.
    """
    from capabledeputy.cli._managed_config import (
        register_default_assistant_surface,
        user_default_daemon_config_path,
    )

    if no_sandbox and force_sandbox:
        err_console.print(
            "[red]--no-sandbox and --force-sandbox are mutually exclusive[/red]",
        )
        raise typer.Exit(code=2)

    daemon_yaml = user_default_daemon_config_path()
    include_sandbox = False if no_sandbox else True if force_sandbox else None
    console.print("[bold]registering bundled assistant tools:[/bold]")
    for msg in register_default_assistant_surface(
        daemon_yaml,
        include_sandbox=include_sandbox,
    ):
        console.print(f"  [dim]·[/dim] {msg}")
    console.print(
        f"\n[green]daemon config: {daemon_yaml}[/green]\n"
        "[bold]next:[/bold] [bold]capdep chat[/bold] — tools available "
        "automatically.",
    )


@app.command("imap-setup")
def imap_setup(
    host: Annotated[
        str,
        typer.Option(help="IMAP host (default: imap.gmail.com)"),
    ] = "imap.gmail.com",
    port: Annotated[int, typer.Option(help="IMAP port (default: 993)")] = 993,
    username: Annotated[str, typer.Option(help="Email address (prompted if omitted)")] = "",
    smtp_host: Annotated[
        str,
        typer.Option(help="SMTP host (default: smtp.gmail.com)"),
    ] = "smtp.gmail.com",
    smtp_port: Annotated[int, typer.Option(help="SMTP port (default: 465)")] = 465,
    register_only: Annotated[
        bool,
        typer.Option(
            "--register-only",
            help=(
                "Skip credential prompts; only add/refresh the IMAP block "
                "in the user-local daemon config. Use when credentials are "
                "already written and you just want `capdep chat` to pick "
                "the server up."
            ),
        ),
    ] = False,
    no_register: Annotated[
        bool,
        typer.Option(
            "--no-register",
            help=(
                "Write only the credentials, NOT the daemon-config managed "
                "block. The IMAP server won't load until you add it to "
                "your daemon config by hand or run --register-only."
            ),
        ),
    ] = False,
) -> None:
    """Set up the IMAP + SMTP config for the bundled imap MCP server.

    For Gmail with 2FA: generate an App Password at
    https://myaccount.google.com/apppasswords and paste it when
    prompted. No OAuth / Cloud Console setup required.

    Writes (default):
      ~/.config/capabledeputy/secrets/imap-config.yaml   (mode 0600)
      ~/.config/capabledeputy/secrets/imap-password      (mode 0600)
      ~/.config/capabledeputy/daemon.yaml                (managed block)

    With `--register-only` only the daemon.yaml managed block is
    refreshed (credentials must already exist). With `--no-register`
    only the credentials are written.

    Once registered, `capdep chat` (no args) and `capdep daemon start`
    (no --config) will auto-load this server.
    """
    import os as _os
    from pathlib import Path

    from capabledeputy.cli._managed_config import (
        IMAP_BLOCK_BODY,
        IMAP_BLOCK_ID,
        imap_credentials_present,
        user_default_daemon_config_path,
        write_managed_block,
    )

    config_dir = (
        Path(_os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "capabledeputy"
    )
    secrets_dir = config_dir / "secrets"

    if register_only:
        if not imap_credentials_present():
            err_console.print(
                "[red]--register-only refused:[/red] no IMAP credentials at "
                f"{secrets_dir / 'imap-config.yaml'}. Run [bold]capdep imap-setup[/bold] "
                "first to write them.",
            )
            raise typer.Exit(code=2)
    else:
        secrets_dir.mkdir(parents=True, exist_ok=True)
        if not username:
            username = typer.prompt("Email address")
        password = typer.prompt(
            "Password (App Password for Gmail; will be hidden)",
            hide_input=True,
        )

        pw_path = secrets_dir / "imap-password"
        pw_path.write_text(password.strip() + "\n", encoding="utf-8")
        pw_path.chmod(0o600)

        cfg_path = secrets_dir / "imap-config.yaml"
        cfg_path.write_text(
            f"""# IMAP / SMTP config for the bundled imap MCP server.
# Generated by `capdep imap-setup` on demand.

imap:
  host: {host}
  port: {port}
  username: {username}
  password_file: {pw_path}

smtp:
  host: {smtp_host}
  port: {smtp_port}
  username: {username}
  password_file: {pw_path}
""",
            encoding="utf-8",
        )
        cfg_path.chmod(0o600)

        console.print(f"[green]wrote IMAP config to {cfg_path}[/green]")
        console.print(f"[green]wrote password to {pw_path} (mode 0600)[/green]")

    if no_register:
        console.print(
            "\n[dim]--no-register: daemon.yaml not touched. Re-run with "
            "--register-only when ready.[/dim]",
        )
        return

    from capabledeputy.cli._managed_config import register_default_assistant_surface

    daemon_yaml = user_default_daemon_config_path()
    replaced, changed = write_managed_block(daemon_yaml, IMAP_BLOCK_ID, IMAP_BLOCK_BODY)
    if changed and replaced:
        console.print(f"[green]refreshed IMAP block in {daemon_yaml}[/green]")
    elif changed:
        console.print(f"[green]registered IMAP block in {daemon_yaml}[/green]")
    else:
        console.print(f"[dim]IMAP block in {daemon_yaml} already up to date[/dim]")

    # Also register the standard bundled-server surface (fs, memory,
    # git, fetch, search) + sandbox if podman is available. One setup
    # run, complete assistant.
    console.print("\n[bold]registering bundled assistant tools:[/bold]")
    for msg in register_default_assistant_surface(daemon_yaml):
        console.print(f"  [dim]·[/dim] {msg}")

    console.print(
        "\n[bold]next:[/bold] [bold]capdep chat[/bold] — Gmail + fs + memory + "
        "git + fetch + search tools will be available automatically.",
    )


@app.command("gworkspace-setup")
def gworkspace_setup(
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help=(
                "Integration mode: official remote Google MCP servers, or community gws-mcp-server."
            ),
        ),
    ] = "official",
    register_only: Annotated[
        bool,
        typer.Option(
            "--register-only",
            help=(
                "Skip the install/auth checklist; only add/refresh the "
                "Google Workspace block in the user-local daemon config. "
                "Use after Google Cloud APIs and OAuth tokens are already configured."
            ),
        ),
    ] = False,
    services: Annotated[
        str,
        typer.Option(
            "--services",
            "-s",
            help=(
                "Comma-separated services. Official mode supports "
                "gmail,drive,calendar,chat,people. Community mode supports "
                "drive,sheets,calendar,docs,gmail."
            ),
        ),
    ] = "",
) -> None:
    """Wire Google Workspace tools into CapDep.

    Default mode registers Google's official remote MCP servers over
    streamable HTTP using CapDep's native OAuth2 browser flow.
    Use `--mode community` only for the legacy local `gws-mcp-server`
    wrapper around the `gws` CLI.
    """
    import shutil

    from capabledeputy.cli._managed_config import (
        GWORKSPACE_BLOCK_ID,
        GWORKSPACE_COMMUNITY_BLOCK_BODY,
        GWORKSPACE_DEFAULT_OFFICIAL_SERVICES,
        google_workspace_official_block_body,
        gws_cli_available,
        gws_mcp_server_available,
        user_default_daemon_config_path,
        write_managed_block,
    )

    mode = mode.strip().lower()
    if mode not in {"official", "community"}:
        err_console.print("[red]--mode must be 'official' or 'community'[/red]")
        raise typer.Exit(code=2)

    if mode == "official":
        services = services or GWORKSPACE_DEFAULT_OFFICIAL_SERVICES
        try:
            block_body = google_workspace_official_block_body(services)
        except ValueError as e:
            err_console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=2) from None
    else:
        default_services = "drive,sheets,calendar,docs,gmail"
        services = services or default_services
        block_body = GWORKSPACE_COMMUNITY_BLOCK_BODY
        if services != default_services:
            block_body = block_body.replace(
                f'"--services", "{default_services}"',
                f'"--services", "{services}"',
            )

    if not register_only:
        console.print(f"[bold]Google Workspace setup ({mode})[/bold]\n")
        if mode == "official":
            if shutil.which("gcloud"):
                console.print("  [green]✓[/green] `gcloud` binary on PATH")
            else:
                console.print(
                    "  [yellow]·[/yellow] `gcloud` not found. Install Google Cloud CLI, "
                    "then enable the Workspace APIs/MCP services.",
                )
            console.print(
                "  [dim]required auth:[/dim] export "
                "[bold]GOOGLE_MCP_CLIENT_ID[/bold] and "
                "[bold]GOOGLE_MCP_CLIENT_SECRET[/bold], then run "
                "[bold]capdep oauth login --server google-gmail[/bold] "
                "(repeat for each enabled Workspace server).",
            )
        else:
            have_gws = gws_cli_available()
            have_mcp_server = gws_mcp_server_available()
            if have_gws:
                console.print("  [green]✓[/green] `gws` binary on PATH")
            else:
                console.print(
                    "  [yellow]·[/yellow] `gws` not found. Install + auth:\n"
                    "      [bold]npm install -g @googleworkspace/cli[/bold]\n"
                    "      [bold]gws auth setup[/bold]\n"
                    "      [bold]gws auth login -s drive,gmail,calendar,docs,sheets[/bold]",
                )
            if have_mcp_server:
                console.print("  [green]✓[/green] `gws-mcp-server` installed")
            else:
                console.print(
                    "  [yellow]·[/yellow] `gws-mcp-server` not found. Install with:\n"
                    "      [bold]npm install -g gws-mcp-server[/bold]",
                )
        console.print()

    daemon_yaml = user_default_daemon_config_path()
    replaced, changed = write_managed_block(daemon_yaml, GWORKSPACE_BLOCK_ID, block_body)
    if changed and replaced:
        console.print(f"[green]refreshed gworkspace block in {daemon_yaml}[/green]")
    elif changed:
        console.print(f"[green]registered gworkspace block in {daemon_yaml}[/green]")
    else:
        console.print(f"[dim]gworkspace block in {daemon_yaml} already up to date[/dim]")

    if mode == "official":
        console.print(
            "\n[bold]next:[/bold] [bold]capdep daemon stop && capdep chat[/bold] — "
            f"official Workspace tools ({services}) will register automatically. "
            "Run [bold]/server[/bold] and [bold]/tools google-[/bold] in the REPL.",
        )
    else:
        console.print(
            "\n[bold]next:[/bold] finish any missing install steps above, then "
            "[bold]capdep daemon stop && capdep chat[/bold]. Run "
            "[bold]/tools gws[/bold] to see loaded community-wrapper tools.",
        )


@app.command("compliance-emit-ssp")
def compliance_emit_ssp(
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Where to write the SSP JSON"),
    ] = "./capdep-ssp.json",
    system_name: Annotated[
        str,
        typer.Option(help="System name for the SSP metadata"),
    ] = "CapableDeputy Personal Agent",
    organization: Annotated[
        str,
        typer.Option(help="Owning organization"),
    ] = "operator",
) -> None:
    """Emit a NIST OSCAL System Security Plan (SSP) for this installation.

    Complements compliance-emit-oscal (which produces the Component
    Definition). The SSP names the system, declares operational
    context, and lists which NIST 800-53 controls CapableDeputy's
    chokepoint rules implement.
    """
    from pathlib import Path

    from capabledeputy.compliance.ssp import emit_system_security_plan
    from capabledeputy.version import __version__

    out_path = Path(output)
    emit_system_security_plan(
        out_path,
        system_name=system_name,
        organization=organization,
        capdep_version=__version__,
    )
    console.print(f"[green]wrote SSP to {out_path}[/green]")


@app.command("compliance-emit-evidence")
def compliance_emit_evidence(
    audit_log: Annotated[
        str,
        typer.Option(
            "--audit-log",
            help="Path to audit.jsonl to use as evidence source",
        ),
    ],
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Where to write the evidence bundle JSON"),
    ] = "./capdep-evidence.json",
) -> None:
    """Emit an audit-evidence bundle grouped by NIST control.

    Reads audit.jsonl and groups policy.decided events under the NIST
    control(s) their rule implements. Auditors get per-control
    evidence trails directly from the daemon's audit log.
    """
    import json as _json
    from pathlib import Path

    from capabledeputy.compliance.ssp import emit_evidence_bundle

    audit_path = Path(audit_log)
    if not audit_path.is_file():
        err_console.print(f"[red]audit log not found: {audit_path}[/red]")
        raise typer.Exit(code=2)

    events: list[dict] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(_json.loads(line))
        except _json.JSONDecodeError:
            continue

    out_path = Path(output)
    emit_evidence_bundle(out_path, events)
    console.print(
        f"[green]wrote evidence bundle from {len(events)} events to {out_path}[/green]",
    )


@app.command("compliance-emit-oscal")
def compliance_emit_oscal(
    output: Annotated[
        str,
        typer.Option(
            "--output",
            "-o",
            help="Where to write the OSCAL bundle JSON",
        ),
    ] = "./capdep-oscal-bundle.json",
    custom_mapping: Annotated[
        str | None,
        typer.Option(
            "--custom-mapping",
            help=(
                "Path to a JSON file extending the default chokepoint-rule → "
                "NIST 800-53 mapping. Operator can add CIS / ISO 27001 / etc. controls."
            ),
        ),
    ] = None,
) -> None:
    """Emit a NIST OSCAL Component Definition documenting the
    CapableDeputy installation's policy implementation.

    Compliance teams ingest the output as standard OSCAL JSON; no
    manual mapping needed. Each chokepoint rule appears as an
    OSCAL control-implementation linked to the NIST 800-53 controls
    it satisfies.
    """
    from pathlib import Path

    from capabledeputy.compliance.oscal import emit_oscal_bundle
    from capabledeputy.version import __version__

    cm: dict[str, list[str]] | None = None
    if custom_mapping:
        import json as _json

        cm_path = Path(custom_mapping)
        cm = _json.loads(cm_path.read_text(encoding="utf-8"))

    out_path = Path(output)
    emit_oscal_bundle(out_path, capdep_version=__version__, custom_mapping=cm)
    console.print(f"[green]wrote OSCAL bundle to {out_path}[/green]")


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


@app.command("mcp-admin-server")
def mcp_admin_server_command(
    socket: str | None = typer.Option(
        None,
        "--socket",
        help="Override daemon socket path",
    ),
) -> None:
    """Run a local admin MCP server for CapableDeputy setup.

    This server is intentionally separate from the session-bound MCP server
    because it can write connector config, store OAuth credentials through the
    daemon, and launch browser login flows.
    """
    from pathlib import Path

    from capabledeputy.mcp_server.admin import serve_admin

    sock = Path(socket) if socket else None
    anyio.run(serve_admin, sock)


@app.command("mcp-control-server")
def mcp_control_server_command(
    socket: str | None = typer.Option(
        None,
        "--socket",
        help="Override daemon socket path",
    ),
) -> None:
    """Run a daemon-control MCP client surface for CapableDeputy.

    This is the MCP equivalent of a CLI/TUI/GUI client: it lets an external
    MCP host inspect sessions, approvals, audit events, setup status, and call
    daemon tools. The daemon remains responsible for policy, approval,
    provenance, and audit enforcement.
    """
    from pathlib import Path

    from capabledeputy.mcp_server.control import serve_control

    sock = Path(socket) if socket else None
    anyio.run(serve_control, sock)


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


# Allow `python -m capabledeputy.cli.main ...` invocation so subprocess
# spawns (e.g., the chat auto-start path) work without depending on a
# `capdep` script being on PATH.
if __name__ == "__main__":
    app()
