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
app.command("chat")(chat_command)
app.command("init")(init_command)
app.command("watch")(watch_command)
audit_app.command("storage-shape")(storage_shape_command)


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


import sys


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
    MCP server. Used for the five `capdep mcp-server-<name>` commands
    that expose CapableDeputy's own Python MCP servers via stdio.

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
            help="Register the sandbox block even if podman isn't on PATH (you'll install it later).",
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
    include_sandbox = (
        False if no_sandbox else True if force_sandbox else None
    )
    console.print("[bold]registering bundled assistant tools:[/bold]")
    for msg in register_default_assistant_surface(
        daemon_yaml, include_sandbox=include_sandbox,
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
    register_only: Annotated[
        bool,
        typer.Option(
            "--register-only",
            help=(
                "Skip the install/auth checklist; only add/refresh the "
                "Google Workspace block in the user-local daemon config. "
                "Use when `gws` is already installed and `gws auth login` "
                "has succeeded."
            ),
        ),
    ] = False,
    services: Annotated[
        str,
        typer.Option(
            "--services",
            "-s",
            help=(
                "Comma-separated services to expose via `gws mcp`. "
                "Default: drive,gmail,calendar,docs,sheets. Use `all` "
                "to expose every Workspace surface (may exceed your "
                "client's tool limit)."
            ),
        ),
    ] = "drive,gmail,calendar,docs,sheets",
) -> None:
    """Wire up the Google Workspace CLI MCP server (`gws mcp`).

    Google maintains the binary and handles OAuth token storage
    (OS keyring, AES-256-GCM at rest); we just spawn `gws mcp` as
    an upstream and proxy stdio through the policy chokepoint.

    Three steps the operator does ONCE:

      1. npm install -g @googleworkspace/cli         # install
      2. gws auth setup                              # one-time gcloud + OAuth client
      3. gws auth login -s drive,gmail,calendar      # browser consent

    Then run this command (or `--register-only` if creds are already
    set up) to write a managed block into
    `~/.config/capabledeputy/daemon.yaml`. After that, `capdep chat`
    automatically loads the official Google Workspace tools alongside
    your other upstreams.

    Tool naming follows Google Discovery API method names (e.g.
    `gws.gmail.users.messages.send`). The block ships with explicit
    overrides for the obvious dangerous calls (send, delete); the
    adapter's name-based inference handles the rest. Audit via
    [bold]/tools gws[/bold] in chat and add overrides as needed —
    user-edits OUTSIDE the managed markers are preserved across
    re-registration.
    """
    from capabledeputy.cli._managed_config import (
        GWORKSPACE_BLOCK_BODY,
        GWORKSPACE_BLOCK_ID,
        gws_cli_available,
        user_default_daemon_config_path,
        write_managed_block,
    )

    have_gws = gws_cli_available()

    # Build the block body with the user's chosen services if non-default.
    block_body = GWORKSPACE_BLOCK_BODY
    if services != "drive,gmail,calendar,docs,sheets":
        # Patch the -s argument in the command line.
        block_body = block_body.replace(
            'command: ["gws", "mcp", "-s", "drive,gmail,calendar,docs,sheets"]',
            f'command: ["gws", "mcp", "-s", "{services}"]',
        )

    if not register_only:
        # Walk-through mode: show the checklist + check what's done.
        console.print("[bold]Google Workspace setup — official CLI path[/bold]\n")
        if have_gws:
            console.print("  [green]✓[/green] `gws` binary on PATH")
        else:
            console.print(
                "  [yellow]·[/yellow] `gws` not found on PATH. Install with:\n"
                "      [bold]npm install -g @googleworkspace/cli[/bold]",
            )
        console.print(
            "  [yellow]·[/yellow] One-time auth setup (needs gcloud CLI):\n"
            "      [bold]gws auth setup[/bold]",
        )
        console.print(
            f"  [yellow]·[/yellow] Browser consent for the services you want:\n"
            f"      [bold]gws auth login -s {services}[/bold]",
        )
        console.print(
            "\nOnce all three steps are done, re-run [bold]capdep gworkspace-setup "
            "--register-only[/bold] to wire the server into your daemon config "
            "(or just continue — we'll write the block now and you can install/"
            "auth afterwards; the daemon will report the missing `gws` binary on "
            "first start until you do).\n",
        )

    daemon_yaml = user_default_daemon_config_path()
    replaced, changed = write_managed_block(daemon_yaml, GWORKSPACE_BLOCK_ID, block_body)
    if changed and replaced:
        console.print(f"[green]refreshed gworkspace block in {daemon_yaml}[/green]")
    elif changed:
        console.print(f"[green]registered gworkspace block in {daemon_yaml}[/green]")
    else:
        console.print(f"[dim]gworkspace block in {daemon_yaml} already up to date[/dim]")

    if have_gws:
        console.print(
            "\n[bold]next:[/bold] [bold]capdep chat[/bold] — Google Workspace "
            f"tools ({services}) will be available automatically.",
        )
    else:
        console.print(
            "\n[bold]next:[/bold] install `gws` (see above) + run `gws auth login`, "
            "then start the daemon. The managed block is in place — it activates "
            "as soon as the binary is reachable.",
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
