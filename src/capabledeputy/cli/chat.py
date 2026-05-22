"""Interactive REPL: `capdep chat` and `capdep demo` commands.

The REPL is a tight read-eval-print loop over `session.send`. Slash
commands cover the approval *and* session-control workflows so a user
can complete a full "agent does X → policy blocks → user reviews and
approves → action runs in a purpose-limited session" arc from a
single terminal.

Slash commands (none of these are visible to the LLM — they're
user-driven daemon passthroughs, identical in authority to the
equivalent `capdep` subcommands):

  Session control:
    /sessions               list all sessions
    /session [id]           details on current or another session
    /switch <id>            retarget the REPL
    /whoami                 print current session id
    /spawn <intent>         create a clean trusted child session
                            and switch to it
    /grant <KIND> <pattern> grant a capability to the current session
                              flags: --one-shot, --destructive,
                                     --max-amount N
    /status                 labels + caps + used_kinds for current
    /labels                 just labels for current
    /caps                   just capabilities for current

  Approvals:
    /approvals              list pending approvals
    /approve <id>           verbatim payload → y/N → approve
    /deny <id>              deny a pending approval
    /submit                 interactively submit an approval

  Observability:
    /trace                  re-render the last turn's tool outcomes
    /audit [N]              last N audit events for current session
    /tools [filter]         list tools currently available (optionally
                            filtered by substring)

  Misc:
    /help                   this list
    /quit                   exit

Anything not starting with `/` is sent to the agent as a user
message — the only path through the LLM is the non-slash one.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import anyio
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from capabledeputy.cli.completer import CapDepCompleter, CompletionCache
from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.presentation import (
    DENY_RECOVERY,
    compartment_summary,
    label_style,
    render_labels,
)

console = Console()
err_console = Console(stderr=True)


_DECISION_COLOR = {
    "allow": "green",
    "deny": "red",
    "require_approval": "yellow",
}


# ---- label palette ------------------------------------------------------
# Single source of truth lives in capabledeputy.presentation so the
# REPL and the TUI render the security model identically. These module
# names are kept (aliases) for the REPL's internal call sites and tests.

_label_rich_style = label_style
_render_labels_rich = render_labels
_compartment_summary = compartment_summary
# Superset of the prior REPL map — also covers capability-expired and
# rate-limit-exceeded. Existing callers/tests only assert presence of
# the hard-deny rules, so the superset is compatible and strictly
# better (more recoveries surfaced).
_DENY_RECOVERY = DENY_RECOVERY


def _client() -> DaemonClient:
    return DaemonClient(default_socket_path())


def _call(method: str, params: dict[str, Any] | None = None) -> Any:
    return anyio.run(_client().call, method, params or {})


def _ensure_daemon(autostart: bool = False, config: str | None = None) -> None:
    """Verify the daemon is reachable; optionally auto-start it.

    When autostart=True, spawns the daemon in the background and polls
    until the socket comes up (30s timeout). Logs to a temp file so
    the operator can debug startup failures.

    Config resolution mirrors the daemon's own: explicit `config` arg
    wins; otherwise CAPDEP_CONFIG; otherwise the user-local default at
    `~/.config/capabledeputy/daemon.yaml` if present. The chosen path
    is printed so it's never a mystery which tools the daemon will
    load.
    """
    from capabledeputy.cli._managed_config import (
        imap_credentials_present,
        has_managed_block,
        IMAP_BLOCK_ID,
        resolve_daemon_config_with_source,
        user_default_daemon_config_path,
    )

    resolved_path, source = resolve_daemon_config_with_source(config)

    # Drift warning: credentials exist but the daemon config doesn't
    # reference them — the user did setup once, then maybe edited the
    # file by hand. Show the one-liner that fixes it.
    if imap_credentials_present():
        if resolved_path is None or not has_managed_block(resolved_path, IMAP_BLOCK_ID):
            console.print(
                "[yellow]heads up:[/yellow] IMAP credentials are stashed but the daemon "
                "config doesn't reference them. Run "
                "[bold]capdep imap-setup --register-only[/bold] to wire them in.",
            )

    # LLM key pre-flight: warn upfront if we're about to autostart a
    # daemon with no key wired. The daemon will also log this, but
    # surfacing it before the user types a message saves a confusing
    # round-trip with the LLM rejecting an unauthenticated call.
    # NB: don't use `Path` here — it's locally re-imported in the
    # autostart branch below, which makes Python treat `Path` as a
    # function-local name everywhere in this function. Use `pathlib`
    # via a uniquely-named alias instead.
    import os as _os
    from pathlib import Path as _PreflightPath

    from capabledeputy.secrets import DEFAULT_KEY_FILENAME

    has_env_key = bool(_os.environ.get("ANTHROPIC_API_KEY"))
    has_file_key = _PreflightPath.cwd().joinpath(DEFAULT_KEY_FILENAME).is_file()
    if autostart and not has_env_key and not has_file_key:
        console.print(
            "[yellow]heads up:[/yellow] no ANTHROPIC_API_KEY in env and no "
            f"{DEFAULT_KEY_FILENAME} in cwd ({_PreflightPath.cwd()}). The agent's LLM "
            "calls will fail. Either `export ANTHROPIC_API_KEY=...` first, "
            f"or place the key in ./{DEFAULT_KEY_FILENAME}.",
        )

    try:
        _call("ping")
        if resolved_path is not None:
            console.print(
                f"[dim]daemon already running (config when started would have been "
                f"{resolved_path} via {source})[/dim]",
            )
        # Issue #10 — daemon version mismatch warning. If the running
        # daemon was started with code older than what's now on disk,
        # the operator may be debugging against stale behavior. Warn
        # before the user types a message and gets confused.
        _warn_on_daemon_drift()
        return
    except DaemonNotRunningError:
        if not autostart:
            err_console.print(
                "[red]daemon not running.[/red] start it in another terminal "
                "with [bold]capdep daemon start[/bold] and re-run this command.\n"
                "Or pass [bold]--autostart[/bold] to spawn one in the background.",
            )
            raise typer.Exit(code=2) from None

    # Autostart path: spawn in background, poll. The daemon has to do
    # schema migration + upstream MCP subprocess spawns + label config
    # parsing before the socket is up, so the timeout has to be wider
    # than the trivial "process started" check. 30s covers real-world
    # configs (5 bundled servers + Google Workspace + IMAP).
    import subprocess
    import sys
    import time
    from pathlib import Path

    console.print("[green]starting daemon in background...[/green]")
    if resolved_path is not None:
        label = (
            "user default" if source == "user-default" else source
        )
        console.print(f"[dim]using daemon config: {resolved_path} ({label})[/dim]")
    else:
        console.print(
            "[dim]no daemon config found — bundled tools only. Run "
            "[bold]capdep imap-setup[/bold] to wire in Gmail.[/dim]",
        )
    cmd = [sys.executable, "-m", "capabledeputy.cli.main", "daemon", "start"]
    # Pass the resolved path explicitly so the child daemon doesn't
    # re-resolve (and we get one source-of-truth in the log).
    if resolved_path is not None:
        cmd.extend(["--config", str(resolved_path)])
    log_path = Path("/tmp") / f"capdep-daemon-{int(time.time())}.log"
    log_file = log_path.open("w")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )
    console.print(f"[dim]daemon log: {log_path}[/dim]")

    # Poll for socket readiness; if the subprocess exits early, surface
    # that immediately instead of waiting for the timeout.
    deadline_steps = 150  # 150 * 0.2s = 30s
    for _ in range(deadline_steps):
        time.sleep(0.2)
        # Bail out early on subprocess exit
        if proc.poll() is not None:
            log_file.close()
            try:
                tail = log_path.read_text(encoding="utf-8")[-2000:]
            except Exception:
                tail = "(could not read log)"
            err_console.print(
                f"[red]daemon process exited early (code {proc.returncode})[/red]\n"
                f"[dim]log tail ({log_path}):[/dim]\n{tail}",
            )
            raise typer.Exit(code=2) from None
        try:
            _call("ping")
            console.print("[green]daemon ready[/green]")
            return
        except DaemonNotRunningError:
            continue
    log_file.close()
    try:
        tail = log_path.read_text(encoding="utf-8")[-2000:]
    except Exception:
        tail = "(could not read log)"
    err_console.print(
        f"[red]daemon failed to start within 30s[/red]\n[dim]log tail ({log_path}):[/dim]\n{tail}",
    )
    raise typer.Exit(code=2) from None


_TURN_COUNTER = {"n": 0}


def _render_outcomes_table(outcomes: list[dict[str, Any]]) -> None:
    """Compact aligned table instead of loose lines, so a multi-tool
    turn stays scannable. DENY/approval reasons + recovery hints are
    rendered under the table where they won't be missed."""
    if not outcomes:
        return
    table = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
    table.add_column("", width=2)
    table.add_column("decision")
    table.add_column("tool")
    table.add_column("rule / labels", overflow="fold")
    for o in outcomes:
        decision = o["decision"]
        color = _DECISION_COLOR.get(decision, "white")
        glyph = {"allow": "✓", "deny": "✗", "require_approval": "⚠"}.get(
            decision,
            "·",
        )
        detail = ""
        if o.get("rule"):
            detail = f"rule={o['rule']}"
        if o.get("labels_added"):
            added = " ".join(
                f"[{_label_rich_style(lbl)}]+{lbl}[/{_label_rich_style(lbl)}]"
                for lbl in o["labels_added"]
            )
            detail = f"{detail}  {added}" if detail else added
        table.add_row(
            f"[{color}]{glyph}[/{color}]",
            f"[{color}]{decision}[/{color}]",
            f"[bold]{o.get('tool_name') or '?'}[/bold]",
            detail,
        )
    console.print(table)

    # Per-outcome detail lines below the table: reasons + actionable
    # recovery for denials, errors for failed dispatches.
    for o in outcomes:
        decision = o["decision"]
        if decision == "deny":
            if o.get("reason"):
                console.print(f"  [dim]{o['reason']}[/dim]")
            # Issue #3: prefer structured recovery_steps from the
            # engine if present; fall back to the static prose hints
            # for back-compat with audit events that lack the new field.
            _render_recovery_steps(o.get("recovery_steps"), o.get("rule"))
        elif decision == "require_approval" and o.get("reason"):
            console.print(f"  [dim]{o['reason']}[/dim]")
        # The agent is told to policy.preview before outbound/destructive
        # calls. A preview that returns decision=deny means the agent
        # correctly chose NOT to make the real call — so there's no
        # `deny` row to hang the hint on. Surface it from the preview
        # output instead, so "what would have been blocked + how to
        # recover" is still actionable, not just buried in agent prose.
        if (
            o.get("tool_name") == "policy.preview"
            and isinstance(o.get("output"), dict)
            and o["output"].get("decision") == "deny"
        ):
            out = o["output"]
            rule = out.get("rule") or "?"
            console.print(
                f"  [yellow]⊘ preview:[/yellow] [dim]{rule} would DENY this "
                f"action — agent correctly skipped the real call[/dim]",
            )
            _render_recovery_steps(out.get("recovery_steps"), rule)
        if o.get("error"):
            console.print(f"  [red]error:[/red] {o['error']}")


def _render_recovery_steps(steps: Any, fallback_rule: str | None) -> None:
    """Render Issue #3 recovery steps as literal pasteable commands.

    On terminals that support OSC 8 hyperlinks (Ghostty, kitty,
    iTerm2, WezTerm, modern xterm — detected by `terminal_caps.caps()`),
    each recovery command is wrapped in a `capdep://paste/<cmd>` URI
    so the terminal can paste-on-click. Terminals without OSC 8
    silently swallow the escape; the rendered text stays pasteable
    via mouse-select-and-paste.

    Falls back to the static `DENY_RECOVERY` prose hint if the
    decision didn't carry structured steps (e.g. older audit events,
    or rules the synthesizer doesn't know yet)."""
    from urllib.parse import quote

    from capabledeputy.cli.terminal_caps import caps as _caps

    use_hyperlinks = _caps().hyperlinks

    if steps:
        console.print("  [cyan]↳ recover:[/cyan]")
        for s in steps:
            cmd = s.get("command") if isinstance(s, dict) else getattr(s, "command", "")
            args = s.get("args") if isinstance(s, dict) else getattr(s, "args", ())
            rationale = (
                s.get("rationale") if isinstance(s, dict) else getattr(s, "rationale", "")
            )
            arg_str = " ".join(args) if args else ""
            command_line = f"{cmd} {arg_str}".strip()
            if use_hyperlinks:
                # Rich [link=...] emits OSC 8. capdep:// URI scheme is
                # intercepted by future terminal-integration work; for
                # now most terminals show it as a tooltip / context-menu
                # copy target — which is the immediate UX win.
                uri = f"capdep://paste/{quote(command_line)}"
                rendered = f"[bold][link={uri}]{command_line}[/link][/bold]"
            else:
                rendered = f"[bold]{command_line}[/bold]"
            console.print(f"     {rendered}  [dim]· {rationale}[/dim]")
        return
    # Fallback to legacy prose hint
    hint = _DENY_RECOVERY.get(fallback_rule or "")
    if hint:
        console.print(f"  [cyan]↳ recover:[/cyan] [dim]{hint}[/dim]")


def _render_turn(result: dict[str, Any]) -> None:
    _TURN_COUNTER["n"] += 1
    console.rule(
        f"[dim]turn {_TURN_COUNTER['n']} · "
        f"iters={result['iterations']} · {result['finish_reason']}[/dim]",
        align="left",
        style="dim",
    )
    # Issue #16 track 1: render agent output as markdown so headings,
    # lists, code blocks render correctly. The agent's response often
    # uses markdown structure for clarity; the plain-text fallback
    # was visually noisy.
    from rich.markdown import Markdown

    content = result["content"]
    console.print("[bold cyan]agent[/bold cyan]")
    console.print(Markdown(content, code_theme="monokai"))
    _render_outcomes_table(result.get("tool_outcomes", []))


def _list_approvals(status: str = "pending") -> list[dict[str, Any]]:
    result = _call("approval.list", {"status": status})
    return result["approvals"]


def _render_approvals(approvals: list[dict[str, Any]]) -> None:
    if not approvals:
        console.print("[dim]no pending approvals[/dim]")
        return
    table = Table(title=f"Pending approvals ({len(approvals)})")
    table.add_column("ID")
    table.add_column("Action")
    table.add_column("Target")
    table.add_column("Payload preview")
    for a in approvals:
        preview = a["payload"][:60]
        if len(a["payload"]) > 60:
            preview += "…"
        table.add_row(str(a["id"]), a["action"], a["target"], preview)
    console.print(table)


def _handle_approve(arg: str) -> None:
    if not arg.strip():
        pending = _list_approvals()
        if not pending:
            # The common confusion: user hit a DENY (e.g.
            # untrusted-meets-egress) and reflexively reached for
            # /approve. There is nothing to approve — DENY is a hard
            # block, not a gate. Explain the distinction and point at
            # the actual recovery path.
            console.print(
                "[yellow]nothing to approve[/yellow] — the approval queue is empty.",
            )
            console.print(
                "[dim]If the agent was just blocked: a [bold]DENY[/bold] "
                "(untrusted/health/financial → egress, or revoked-by-use) "
                "is a hard block — it cannot be approved. Recover with "
                "[bold]/spawn[/bold] (clean session) or [bold]/extract[/bold] "
                "(declassify a fact). Only [bold]REQUIRE_APPROVAL[/bold] "
                "gates (purchases, destructive ops) produce something to "
                "/approve.[/dim]",
            )
            return
        ids = ", ".join(f"#{p['id']}" for p in pending)
        err_console.print(
            f"[red]usage:[/red] /approve <id> — pending: {ids}",
        )
        return
    try:
        approval_id = int(arg.strip())
    except ValueError:
        err_console.print(f"[red]not an id:[/red] {arg!r}")
        return
    # Show verbatim payload before approving so the demo highlights the
    # "you see exactly what would happen" property.
    show = _call("approval.show", {"id": approval_id})
    console.print(
        Panel(
            show["payload"],
            title=f"approval #{approval_id}: {show['action']} → {show['target']}",
            border_style="yellow",
        ),
    )
    confirm = Prompt.ask("approve? [y/N]", default="N").strip().lower()
    if confirm not in ("y", "yes"):
        console.print("[dim]not approving[/dim]")
        return
    result = _call("approval.approve", {"id": approval_id})
    console.print(f"[green]✓ approved[/green] approval #{approval_id}")
    if result.get("executed_in_session"):
        console.print(
            f"  dispatched in purpose session [bold]{result['executed_in_session'][:8]}[/bold]",
        )
        dispatch = result.get("dispatch", {})
        if dispatch.get("error"):
            console.print(f"  [red]dispatch error:[/red] {dispatch['error']}")
        else:
            console.print(f"  [green]dispatch decision:[/green] {dispatch.get('decision')}")


def _handle_deny(arg: str) -> None:
    if not arg.strip():
        err_console.print("[red]usage:[/red] /deny <id>")
        return
    try:
        approval_id = int(arg.strip())
    except ValueError:
        err_console.print(f"[red]not an id:[/red] {arg!r}")
        return
    _call("approval.deny", {"id": approval_id, "reason": "denied via REPL"})
    console.print(f"[yellow]denied[/yellow] approval #{approval_id}")


def _handle_sessions() -> None:
    result = _call("session.list")
    sessions = result.get("sessions", [])
    if not sessions:
        console.print("[dim]no sessions[/dim]")
        return
    table = Table(title=f"Sessions ({len(sessions)})")
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Intent")
    table.add_column("Labels")
    for s in sessions:
        table.add_row(
            s["id"][:8],
            s["status"],
            (s.get("intent") or "")[:40],
            ", ".join(s.get("label_set", [])) or "-",
        )
    console.print(table)


def _handle_session_show(target_id: str) -> None:
    result = _call("session.get", {"session_id": target_id})
    console.print(f"[bold]session[/bold] {result['id']}")
    console.print(f"  status:  {result['status']}")
    if result.get("intent"):
        console.print(f"  intent:  {result['intent']}")
    if result.get("parent"):
        console.print(f"  parent:  {result['parent']}")
    if result.get("label_set"):
        console.print(f"  labels:  {', '.join(result['label_set'])}")
    caps = result.get("capability_set", [])
    if caps:
        console.print(f"  caps:    {len(caps)} granted")
        for c in caps:
            console.print(
                f"    - {c['kind']} pattern={c['pattern']}{_constraint_markers(c)}",
            )


def _resolve_session_id(prefix: str) -> str | None:
    """Resolve a possibly-truncated session id to its full UUID by
    consulting the daemon. Returns None if no unique match."""
    result = _call("session.list")
    matches = [s["id"] for s in result.get("sessions", []) if s["id"].startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        err_console.print(f"[red]no session matches:[/red] {prefix}")
    else:
        err_console.print(
            f"[red]ambiguous session id:[/red] {prefix} ({len(matches)} matches)",
        )
    return None


def _handle_submit(session_id: str) -> None:
    """Interactively submit an approval. Used after the agent loop
    surfaces a require_approval outcome — the user enters the action
    payload by hand. Future versions will auto-extract from the
    triggering tool call."""
    action = Prompt.ask("action (SEND_EMAIL, QUEUE_PURCHASE)").strip()
    target = Prompt.ask("target (e.g. alice@example.com)").strip()
    payload = Prompt.ask("payload (one line)").strip()
    justification = Prompt.ask("justification", default="").strip()
    result = _call(
        "approval.submit",
        {
            "from_session": session_id,
            "action": action,
            "target": target,
            "payload": payload,
            "justification": justification,
        },
    )
    console.print(f"[green]submitted[/green] approval #{result['id']}")


def _send_message(session_id: str, message: str) -> dict[str, Any]:
    return _call("session.send", {"session_id": session_id, "message": message})


_HELP = """slash commands (user-only, never visible to the LLM):

  session: /sessions /session [id] /switch <id> /whoami
           /spawn <intent> [--bare] — clean child (inherits parent
                                       non-destructive caps unless --bare)
           /abort [id]              — abort session (current if no id)
           /grant <KIND> <pattern> [--one-shot --destructive --max-amount N]
           /status /labels /caps

  approval: /approvals  /approve <id>  /deny <id>  /submit
            /remember <ACTION> <target-pattern>  — auto-approve future
                                                   matching gates
            /override request <KIND> <target>    — request operator
                                                   override for a denied
                                                   action (§10.11)
            /override list                       — pending/active grants
            /override show <id>                  — full grant detail

  declassify: /schemas               — list declassification schemas
              /extract <msg> <schema> — quarantined-LLM extraction

  trace:   /trace  /audit [N] [--full]
           /tools [filter]      — list registered tools, grouped by
                                  capability kind (optional substring
                                  filter, e.g. /tools gmail)

  misc:    /help  /quit
"""


def _handle_spawn(arg: str, focus: dict[str, str]) -> None:
    """Create a clean child session with TRUSTED_USER_DIRECT and
    switch the REPL to it.

    By default, **non-destructive copies of the parent's capabilities
    are inherited** — so the agent has tools to work with from turn 1.
    Destructive flags are stripped; one-shot expiries become session
    expiry; the labels are reset to {TRUSTED_USER_DIRECT}.

    Pass `--bare` to spawn with no capabilities — useful when you
    plan to grant exactly what's needed via `/grant` and nothing
    else.

    The parent pointer is always set so the audit trail records the
    lineage.
    """
    parts = arg.split()
    bare = "--bare" in parts
    parts = [p for p in parts if p != "--bare"]
    intent = " ".join(parts).strip() or "user-spawned clean session"

    new = _call("session.new", {"intent": intent, "parent": focus["id"]})
    new_id = new["id"]
    _call(
        "session.add_labels",
        {"session_id": new_id, "labels": ["trusted.user_direct"]},
    )

    inherited = 0
    if not bare:
        # Copy non-destructive forms of the parent's caps so the agent
        # has tools in the new session. Destructive operations still
        # require approval; the child has no automatic permission to
        # bypass the destructive-op gate.
        from uuid import uuid4

        parent_info = _call("session.get", {"session_id": focus["id"]})
        for cap in parent_info.get("capability_set", []):
            cap_copy = {
                "kind": cap["kind"],
                "pattern": cap["pattern"],
                "expiry": "session",
                "origin": "user_approved",
                "audit_id": str(uuid4()),
                "max_amount": cap.get("max_amount"),
                "allows_destructive": False,
                "revoked_by": [],
            }
            try:
                _call(
                    "session.grant_capability",
                    {"session_id": new_id, "capability": cap_copy},
                )
                inherited += 1
            except Exception as e:
                err_console.print(
                    f"[yellow]warn:[/yellow] could not inherit "
                    f"{cap['kind']} pattern={cap['pattern']}: {e}",
                )

    focus["id"] = new_id
    focus["label"] = _short_label(new_id)
    cap_note = f"inherited={inherited} caps (non-destructive)" if not bare else "no caps (bare)"
    console.print(
        f"[green]✓ spawned[/green] [cyan]{focus['label']}[/cyan] "
        f"[dim]({new_id[:8]}, parent={new.get('parent', '?')[:8]}, "
        f"labels=trusted.user_direct, {cap_note})[/dim]",
    )
    if bare:
        console.print(
            "[dim]bare session has no capabilities. agent has no "
            "tools until you [bold]/grant[/bold] some.[/dim]",
        )


def _handle_grant(arg: str, session_id: str) -> None:
    """/grant <KIND> <pattern> [--one-shot --destructive --max-amount N --ttl S]

    Builds a Capability and ships it to session.grant_capability. The
    cap's audit_id is generated daemon-side from the serialized payload.
    `--ttl S` sets an absolute expiry S seconds from now; the policy
    engine then denies the capability deterministically once that
    deadline passes (rule capability-expired).
    """
    parts = arg.split()
    if len(parts) < 2:
        err_console.print(
            "[red]usage:[/red] /grant <KIND> <pattern> "
            "[--one-shot] [--destructive] [--max-amount N] [--ttl SECONDS]",
        )
        return
    kind, pattern = parts[0].upper(), parts[1]
    rest = parts[2:]
    one_shot = "--one-shot" in rest
    allows_destructive = "--destructive" in rest
    max_amount: int | None = None
    if "--max-amount" in rest:
        i = rest.index("--max-amount")
        try:
            max_amount = int(rest[i + 1])
        except (IndexError, ValueError):
            err_console.print("[red]--max-amount needs a number[/red]")
            return

    expires_at: str | None = None
    ttl_secs: int | None = None
    if "--ttl" in rest:
        i = rest.index("--ttl")
        try:
            ttl_secs = int(rest[i + 1])
        except (IndexError, ValueError):
            err_console.print("[red]--ttl needs a number of seconds[/red]")
            return
        expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_secs)).isoformat()

    rate_limit: dict[str, int] | None = None
    rate_desc: str | None = None
    if "--rate" in rest:
        i = rest.index("--rate")
        try:
            spec = rest[i + 1]
            n_str, w_str = spec.split("/")
            mx, win = int(n_str), int(w_str)
            if mx <= 0 or win <= 0:
                raise ValueError
        except (IndexError, ValueError):
            err_console.print(
                "[red]--rate needs MAX/WINDOW_SECONDS, both > 0 (e.g. --rate 5/60)[/red]",
            )
            return
        rate_limit = {"max_uses": mx, "window_seconds": win}
        rate_desc = f"{mx}/{win}s"

    from uuid import uuid4

    cap = {
        "kind": kind,
        "pattern": pattern,
        "expiry": "one_shot" if one_shot else "session",
        "origin": "user_approved",
        "audit_id": str(uuid4()),
        "max_amount": max_amount,
        "allows_destructive": allows_destructive,
        "revoked_by": [],
        "expires_at": expires_at,
        "rate_limit": rate_limit,
    }
    try:
        _call(
            "session.grant_capability",
            {"session_id": session_id, "capability": cap},
        )
    except Exception as e:
        err_console.print(f"[red]grant failed:[/red] {e}")
        return
    console.print(
        f"[green]✓ granted[/green] {kind} pattern={pattern}"
        + (" [yellow](one-shot)[/yellow]" if one_shot else "")
        + (" [yellow](destructive)[/yellow]" if allows_destructive else "")
        + (f" [yellow](expires in {ttl_secs}s)[/yellow]" if ttl_secs is not None else "")
        + (f" [yellow](rate {rate_desc})[/yellow]" if rate_desc else ""),
    )


def _expiry_marker(cap: dict[str, Any], *, now: datetime | None = None) -> str:
    """Rich-markup suffix for a capability dict: empty if non-expiring,
    a remaining-time hint if still valid, or '(expired)' once the
    half-open deadline has passed. Shared by /status, /caps, and
    /session so every inspection view annotates consistently."""
    raw = cap.get("expires_at")
    if not raw:
        return ""
    deadline = datetime.fromisoformat(raw)
    ref = now or datetime.now(UTC)
    if ref >= deadline:
        return " [red](expired)[/red]"
    secs = int((deadline - ref).total_seconds())
    return f" [yellow](expires in {secs}s)[/yellow]"


def _rate_marker(cap: dict[str, Any]) -> str:
    """Rich-markup suffix describing a capability's sliding-window
    rate limit, or empty if unlimited. Shared by every inspection
    view alongside _expiry_marker."""
    rl = cap.get("rate_limit")
    if not rl:
        return ""
    return f" [yellow](rate {rl['max_uses']}/{rl['window_seconds']}s)[/yellow]"


def _constraint_markers(cap: dict[str, Any]) -> str:
    return f"{_expiry_marker(cap)}{_rate_marker(cap)}"


def _handle_status(session_id: str, *, only: str | None = None) -> None:
    info = _call("session.get", {"session_id": session_id})
    if only != "caps":
        labels = info.get("label_set", [])
        word, style = _compartment_summary(labels)
        console.print(
            f"[bold]compartment[/bold] [{style}]{word}[/{style}] "
            f"({len(labels)}): {_render_labels_rich(labels)}",
        )
        used = info.get("used_kinds", [])
        if used:
            console.print(f"[bold]used kinds[/bold]: {', '.join(used)}")
    if only != "labels":
        caps = info.get("capability_set", [])
        if not caps:
            console.print("[bold]capabilities[/bold]: [dim]none[/dim]")
        else:
            console.print(f"[bold]capabilities[/bold] ({len(caps)}):")
            for c in caps:
                extras = []
                if c.get("max_amount"):
                    extras.append(f"max={c['max_amount']}")
                if c.get("allows_destructive"):
                    extras.append("destructive")
                if c.get("expiry") == "one_shot":
                    extras.append("one-shot")
                tail = f" [{', '.join(extras)}]" if extras else ""
                console.print(
                    f"  - {c['kind']} pattern={c['pattern']}{tail}{_constraint_markers(c)}",
                )


def _pending_approval_ids(outcomes: list[dict[str, Any]]) -> list[int]:
    """The runtime registers approvals at the policy chokepoint (see
    LabeledToolClient). The REPL does NOT submit anything — it just
    observes the `approval_id` the runtime already queued and routes
    the user to /approve.

    Outcomes that are require_approval but carry no approval_id are
    tools whose definition declares no approval_route; the user falls
    back to /submit for those.
    """
    ids: list[int] = []
    for o in outcomes:
        if o.get("decision") != "require_approval":
            continue
        aid = o.get("approval_id")
        if aid is not None:
            ids.append(int(aid))
    return ids


def _handle_remember(arg: str) -> None:
    """/remember <ACTION> <target-glob> [--label-includes L1,L2,...]
                  [--tag TAG] [--ttl-hours N]

    Install an auto-approval pattern (Issue #8). Example:
      /remember QUEUE_PURCHASE amazon.com
      /remember SEND_EMAIL marc@joneslaw.io --label-includes confidential.personal --tag self-forward

    With --label-includes, the pattern auto-approves only when the
    request's incoming labels include EVERY listed label. Lets you
    declare 'self-forwarding tainted personal content is fine' as a
    distinct rule from 'self-forwarding anything'.
    """
    parts = arg.split()
    if len(parts) < 2:
        err_console.print(
            "[red]usage:[/red] /remember <ACTION> <target-pattern> "
            "[--label-includes L1,L2] [--tag TAG] [--ttl-hours N]\n"
            "  e.g. /remember QUEUE_PURCHASE amazon*\n"
            "       /remember SEND_EMAIL marc@joneslaw.io "
            "--label-includes confidential.personal --tag self-forward",
        )
        return
    action, target_pattern = parts[0].upper(), parts[1]
    labels_required: list[str] = []
    audit_tag = ""
    ttl_hours = 24
    i = 2
    while i < len(parts):
        tok = parts[i]
        if tok == "--label-includes" and i + 1 < len(parts):
            labels_required = [s.strip() for s in parts[i + 1].split(",") if s.strip()]
            i += 2
        elif tok == "--tag" and i + 1 < len(parts):
            audit_tag = parts[i + 1]
            i += 2
        elif tok == "--ttl-hours" and i + 1 < len(parts):
            try:
                ttl_hours = int(parts[i + 1])
            except ValueError:
                err_console.print(
                    f"[red]--ttl-hours expects an integer; got {parts[i + 1]!r}[/red]",
                )
                return
            i += 2
        else:
            err_console.print(f"[red]unknown /remember flag:[/red] {tok}")
            return

    params = {
        "action": action,
        "target_pattern": target_pattern,
        "ttl_hours": ttl_hours,
    }
    if labels_required:
        params["labels_required"] = labels_required
    if audit_tag:
        params["audit_tag"] = audit_tag

    try:
        # Issue #8 fix: the RPC is approval_pattern.create, not the
        # `pattern.create` the prior code referenced (which never
        # resolved — pre-existing bug fixed here).
        result = _call("approval_pattern.create", params)
    except Exception as e:
        err_console.print(f"[red]pattern.create failed:[/red] {e}")
        return
    if result.get("error"):
        err_console.print(f"[red]{result['error']}[/red]")
        return
    label_str = (
        f" labels={','.join(labels_required)}" if labels_required else ""
    )
    tag_str = f" tag={audit_tag}" if audit_tag else ""
    console.print(
        f"[green]✓ pattern[/green] {action} {target_pattern}{label_str}{tag_str} "
        f"→ id={result.get('id', '?')[:8]}",
    )


def _handle_tools(filter_substring: str = "") -> None:
    """/tools [filter] — render the tools the daemon currently exposes,
    grouped by capability kind. Optional substring filter narrows the
    list (e.g. `/tools gmail` shows only gmail.*)."""
    try:
        result = _call("tool.list")
    except Exception as e:
        err_console.print(f"[red]tool.list failed:[/red] {e}")
        return

    tools = result.get("tools", []) or []
    if not tools:
        console.print("[dim]no tools registered[/dim]")
        return

    needle = filter_substring.lower().strip()
    if needle:
        tools = [t for t in tools if needle in (t.get("name") or "").lower()]
    if not tools:
        console.print(f"[dim]no tools match {filter_substring!r}[/dim]")
        return

    # Group by capability kind for scannability.
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for t in tools:
        kind = t.get("capability_kind") or "?"
        by_kind.setdefault(kind, []).append(t)

    console.print(f"[bold]{len(tools)} tool(s) available[/bold]")
    for kind in sorted(by_kind):
        console.print(f"\n[cyan]{kind}[/cyan]")
        for t in sorted(by_kind[kind], key=lambda x: x.get("name", "")):
            name = t.get("name", "?")
            effect = t.get("effect_class") or ""
            effect_str = f"  [dim]({effect})[/dim]" if effect else ""
            console.print(f"  [bold]{name}[/bold]{effect_str}")


def _warn_on_daemon_drift() -> None:
    """Compare the running daemon's captured code version against
    what's currently on disk; warn on drift. Best-effort — failures
    here never block the chat flow."""
    try:
        daemon_version = _call("daemon.code_version", {})
    except Exception:
        return  # older daemons don't expose this RPC; skip silently

    # Capture our current view of the source. Same logic as the
    # daemon's _capture_code_version but called from this process.
    from capabledeputy.daemon.handlers import _capture_code_version

    on_disk = _capture_code_version()

    daemon_hash = (daemon_version or {}).get("manifest_hash", "")
    disk_hash = (on_disk or {}).get("manifest_hash", "")
    daemon_rev = (daemon_version or {}).get("git_rev", "")
    disk_rev = (on_disk or {}).get("git_rev", "")

    if daemon_hash and disk_hash and daemon_hash != disk_hash:
        # Drift detected.
        details = []
        if daemon_rev and disk_rev and daemon_rev != disk_rev:
            details.append(
                f"daemon git={daemon_rev[:8]} vs on-disk git={disk_rev[:8]}",
            )
        elif daemon_version.get("git_dirty") == "dirty" or on_disk.get("git_dirty") == "dirty":
            details.append("uncommitted source changes detected")
        details.append(
            f"manifest {daemon_hash[:8]} vs {disk_hash[:8]}",
        )
        console.print(
            "[yellow]heads up:[/yellow] running daemon was started with code "
            "that differs from current source. "
            f"({'; '.join(details)}) "
            "Restart with [bold]capdep daemon stop && capdep chat[/bold] "
            "to pick up changes.",
        )


def _handle_override(arg: str, session_id: str) -> None:
    """/override [request|list|show] — surface the ApprovalQueue's
    override path (§10.11) from the REPL.

    Subcommands:
      /override request <KIND> <target> [--floor F] [--justification "..."]
      /override list
      /override show <id>

    Floor defaults to `integrity-floor` (the typical label-conflict
    case — `untrusted-meets-egress` etc.). Operators who've set up
    policies for other floors can specify with --floor.
    """
    import getpass

    parts = arg.split()
    if not parts:
        _print_override_help()
        return
    sub = parts[0].lower()

    if sub == "list":
        try:
            result = _call("override.list", {"session_id": session_id})
        except Exception as e:
            err_console.print(f"[red]override.list failed:[/red] {e}")
            return
        grants = result.get("grants", []) or []
        if not grants:
            console.print("[dim]no override grants on this session[/dim]")
            return
        for g in grants:
            console.print(
                f"  [bold]#{g.get('id', '?')[:8]}[/bold] "
                f"{g.get('action_kind', '?')} → {g.get('target', '?')} "
                f"[dim]state={g.get('state', '?')} "
                f"expires={g.get('expires_at', '?')[:19]}[/dim]",
            )
        return

    if sub == "show":
        if len(parts) < 2:
            err_console.print("[red]usage:[/red] /override show <id>")
            return
        try:
            result = _call("override.show", {"id": parts[1]})
        except Exception as e:
            err_console.print(f"[red]override.show failed:[/red] {e}")
            return
        for k, v in result.items():
            console.print(f"  [dim]{k}:[/dim] {v}")
        return

    if sub == "request":
        if len(parts) < 3:
            err_console.print(
                "[red]usage:[/red] /override request <KIND> <target> "
                "[--floor F] [--justification \"...\"]",
            )
            return
        kind = parts[1].upper()
        target = parts[2]
        # Parse remaining flags
        floor = "integrity-floor"
        justification = ""
        i = 3
        while i < len(parts):
            tok = parts[i]
            if tok == "--floor" and i + 1 < len(parts):
                floor = parts[i + 1]
                i += 2
            elif tok == "--justification" and i + 1 < len(parts):
                # Join remaining as justification (quoted strings get
                # split by shlex elsewhere; here we just join)
                justification = " ".join(parts[i + 1 :])
                break
            else:
                i += 1

        params = {
            "session_id": session_id,
            "action_kind": kind,
            "target": target,
            "category": "unknown",
            "tier": "restricted",
            "floor": floor,
            "invoker": getpass.getuser(),
            "friction_confirmed": True,  # typing the command IS the friction
        }
        if justification:
            params["justification"] = justification

        try:
            result = _call("override.request", params)
        except Exception as e:
            err_console.print(f"[red]override.request failed:[/red] {e}")
            return
        if result.get("refused"):
            err_console.print(
                f"[red]override REFUSED:[/red] {result.get('reason', '?')} "
                f"{result.get('detail', '')}",
            )
            return
        grant_id = result.get("id", "?")
        state = result.get("state", "?")
        console.print(
            f"[green]✓ override grant[/green] #{str(grant_id)[:8]} "
            f"[dim](state={state})[/dim]",
        )
        if state == "pending_attestation":
            console.print(
                "[yellow]→ dual-control attestation required.[/yellow] "
                "An authorized second principal must run "
                f"[bold]/override attest {grant_id}[/bold] before the "
                "grant becomes active.",
            )
        return

    _print_override_help()


def _print_override_help() -> None:
    console.print(
        "[bold]/override[/bold] subcommands:\n"
        "  /override request <KIND> <target> [--floor F] [--justification \"...\"]\n"
        "  /override list                — pending/active grants on this session\n"
        "  /override show <id>           — full detail of one grant",
    )


def _handle_schemas() -> None:
    result = _call("extract.schemas")
    schemas = result.get("schemas", [])
    if not schemas:
        console.print("[dim]no schemas available[/dim]")
        return
    console.print("[bold]available declassification schemas:[/bold]")
    for s in schemas:
        console.print(f"  - {s}")


def _handle_extract(arg: str) -> None:
    """/extract <message_id> <schema>

    Runs the quarantined LLM against an inbox message body and returns
    the schema-validated dict. The result carries no labels — the
    user can paste it into a clean spawned session.
    """
    import json as _json

    parts = arg.split()
    if len(parts) < 2:
        err_console.print(
            "[red]usage:[/red] /extract <message_id> <schema> (see /schemas for the list)",
        )
        return
    message_id, schema = parts[0], parts[1]
    result = _call(
        "extract.inbox_message",
        {"message_id": message_id, "schema": schema},
    )
    if "error" in result:
        err_console.print(f"[red]extract failed:[/red] {result['error']}")
        return
    console.print(
        Panel(
            _json.dumps(result["data"], indent=2),
            title=(f"declassified: {result['schema']} from message {result['message_id']}"),
            border_style="green",
        ),
    )
    console.print(
        "[dim]this result carries no labels — paste it into a "
        "/spawn-ed clean session to act on it.[/dim]",
    )


def _handle_abort(arg: str, focus: dict[str, str]) -> None:
    """/abort [id]  — abort a session and switch back to a sensible
    surviving one. With no arg, aborts the current session."""
    target = arg.strip() or focus["id"]
    resolved = target if len(target) >= 32 else _resolve_session_id(target)
    if resolved is None:
        return
    try:
        _call("session.abort", {"session_id": resolved})
    except Exception as e:
        err_console.print(f"[red]abort failed:[/red] {e}")
        return
    console.print(f"[yellow]aborted[/yellow] {resolved[:8]}")
    # If we just aborted the focused session, switch to the most
    # recently updated still-active one (or print a hint if none).
    if resolved == focus["id"]:
        try:
            sessions = _call("session.list", {"status": "active"}).get(
                "sessions",
                [],
            )
        except Exception:
            sessions = []
        if not sessions:
            console.print(
                "[dim]no active sessions left. /quit, then start one with "
                "[bold]capdep demo start <name>[/bold] or [bold]capdep session new[/bold].[/dim]",
            )
            return
        # session.list returns sessions in creation order; pick the latest.
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        new_id = sessions[0]["id"]
        focus["id"] = new_id
        focus["label"] = _short_label(new_id)
        console.print(
            f"[green]→[/green] switched to [cyan]{focus['label']}[/cyan] [dim]({new_id[:8]})[/dim]",
        )


def _handle_audit(arg: str, session_id: str) -> None:
    """`/audit [N] [--full]` — render the last N events for this session.
    Without --full, one line per event with the most relevant payload
    fields surfaced. With --full, dumps each event payload as JSON."""
    import json as _json

    parts = arg.split()
    full = "--full" in parts
    parts = [p for p in parts if p != "--full"]
    try:
        limit = int(parts[0]) if parts else 20
    except ValueError:
        err_console.print(f"[red]not a number:[/red] {parts[0]!r}")
        return

    result = _call(
        "audit.list",
        {"session_id": session_id, "limit": limit},
    )
    events = result.get("events", [])
    if not events:
        console.print("[dim]no events[/dim]")
        return
    for e in events:
        ts = e.get("timestamp", "")[:19]
        et = e.get("event_type", "")
        payload = e.get("payload") or {}
        if full:
            console.print(f"[dim]{ts}[/dim] [bold]{et}[/bold]")
            console.print(f"  {_json.dumps(payload, indent=2)}")
            continue
        # Compact view: cherry-pick the most useful per-event-type fields.
        bits: list[str] = []
        for key in ("tool", "decision", "rule", "kind", "action"):
            if key in payload:
                bits.append(f"{key}={payload[key]}")
        if "labels_added" in payload:
            bits.append(f"labels+={','.join(payload['labels_added'])}")
        if "reason" in payload and payload.get("decision") != "allow":
            reason = str(payload["reason"])
            if reason:
                bits.append(f"reason={reason[:60]}")
        tail = " " + " ".join(bits) if bits else ""
        console.print(f"[dim]{ts}[/dim] {et}{tail}")


def _short_label(session_id: str) -> str:
    """Render a human-meaningful session label for the prompt.

    Prefers `intent` (truncated), falls back to the UUID prefix. The
    daemon round-trip is cheap enough to do once per refresh, and the
    label keeps the user oriented when multiple sessions are open.
    """
    try:
        info = _call("session.get", {"session_id": session_id})
    except Exception:
        return session_id[:8]
    intent = (info.get("intent") or "").strip()
    if intent:
        return intent if len(intent) <= 32 else intent[:30] + "…"
    return session_id[:8]


def _history_path() -> Path:
    p = Path.home() / ".cache" / "capabledeputy" / "repl_history"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _toolbar_label_ansi(label: str) -> str:
    if label.startswith("untrusted."):
        return f"<ansired>{label}</ansired>"
    if label.startswith("confidential."):
        return f"<ansiyellow>{label}</ansiyellow>"
    if label.startswith("trusted."):
        return f"<ansigreen>{label}</ansigreen>"
    if label.startswith("egress."):
        return f"<ansimagenta>{label}</ansimagenta>"
    return label


def _make_bottom_toolbar(cache: CompletionCache, focus: dict[str, str]):
    """Always-visible status band. Reads the CompletionCache (already
    polled every ~1s for the completer) so it's near-live and adds no
    new polling. With refresh_interval set on the prompt, the
    compartment indicator updates while the user just sits there —
    you watch it go red as the agent reads untrusted content."""

    def render() -> HTML:
        sid = focus["id"]
        sess = next((s for s in cache.sessions if s["id"] == sid), None)
        short = sid[:8]
        if sess is None:
            return HTML(f" session <b>{short}</b> · <ansicyan>(syncing…)</ansicyan> ")
        labels = sess.get("label_set", [])
        caps_list = sess.get("capability_set", [])
        ncaps = len(caps_list)
        now = datetime.now(UTC)
        n_bounded = n_expired = 0
        for c in caps_list:
            raw = c.get("expires_at") if isinstance(c, dict) else None
            if not raw:
                continue
            if now >= datetime.fromisoformat(raw):
                n_expired += 1
            else:
                n_bounded += 1
        if n_expired:
            ttl_seg = f" <ansired>ttl {n_bounded}+{n_expired}✗</ansired>"
        elif n_bounded:
            ttl_seg = f" <ansiyellow>ttl {n_bounded}</ansiyellow>"
        else:
            ttl_seg = ""
        npending = len(cache.approval_ids)
        word, _ = _compartment_summary(labels)
        word_tag = {
            "TAINTED": f"<ansired><b>{word}</b></ansired>",
            "confidential": f"<ansiyellow>{word}</ansiyellow>",
            "clean": f"<ansigreen>{word}</ansigreen>",
        }[word]
        if labels:
            comp = " ".join(_toolbar_label_ansi(lbl) for lbl in sorted(labels))
        else:
            comp = "<ansigreen>—</ansigreen>"
        pending_seg = f" │ <ansiyellow><b>⚠ {npending} pending</b></ansiyellow>" if npending else ""
        return HTML(
            f" session <b>{short}</b> "
            f"│ compartment {word_tag}: {comp} "
            f"│ caps {ncaps}{ttl_seg}{pending_seg} ",
        )

    return render


def _inline_approval_review(approval_ids: list[int]) -> None:
    """Issue #7 — inline ApprovalQueue staging UI.

    Surfaces the verbatim payload + labels + rule context at the
    chokepoint moment. Single-key dispatch:
      [a]pprove / [d]eny / [e]dit / [v]iew labels / [s]kip

    The [e]dit path preserves §10.11 immutability: editing creates a
    NEW approval request (new monotonic id, new hash); the original
    is denied as superseded. Operator approves the new one. This is
    the right semantics — approval is for a specific payload, not a
    mutable target.
    """
    import os
    import subprocess
    import tempfile

    from rich.table import Table

    for aid in approval_ids:
        try:
            show = _call("approval.show", {"id": aid})
        except Exception as e:
            err_console.print(f"[red]could not load approval #{aid}:[/red] {e}")
            continue

        # Build the label/context line that prefaces the payload.
        # Labels in/out describe the IFC flow this approval crosses.
        labels_in = show.get("labels_in") or []
        labels_out = show.get("labels_out") or []
        justification = (show.get("justification") or "").strip()

        # Header table — action, target, labels in/out, justification.
        meta = Table.grid(padding=(0, 1))
        meta.add_column(style="dim", no_wrap=True)
        meta.add_column()
        meta.add_row("action:", show.get("action", "?"))
        meta.add_row("target:", show.get("target", "?"))
        if labels_in:
            meta.add_row("labels in:", ", ".join(labels_in))
        if labels_out:
            meta.add_row("labels out:", ", ".join(labels_out))
        if justification:
            meta.add_row("rationale:", justification)
        console.print(meta)

        console.print(
            Panel(
                show["payload"],
                title=f"approval #{aid} · verbatim payload",
                subtitle="[dim]this is exactly what will happen if you approve[/dim]",
                border_style="yellow",
            ),
        )
        choice = (
            Prompt.ask(
                f"  approval #{aid}",
                choices=["a", "d", "e", "v", "s"],
                default="s",
            )
            .strip()
            .lower()
        )

        if choice == "v":
            # Show labels detail and any extra context. After viewing
            # the user gets the choice prompt again.
            console.print(f"  [dim]labels_in :[/dim] {', '.join(labels_in) or '(none)'}")
            console.print(f"  [dim]labels_out:[/dim] {', '.join(labels_out) or '(none)'}")
            console.print(
                f"  [dim]rationale :[/dim] {justification or '(none)'}",
            )
            # Loop back for the actual a/d/e/s decision
            choice = (
                Prompt.ask(
                    f"  approval #{aid}",
                    choices=["a", "d", "e", "s"],
                    default="s",
                )
                .strip()
                .lower()
            )

        if choice == "e":
            # Open the payload in $EDITOR. On save with changes, submit
            # a new approval request and deny the original as superseded.
            editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                prefix=f"capdep-approval-{aid}-",
                delete=False,
            ) as f:
                f.write(show["payload"])
                tmp_path = f.name
            try:
                subprocess.run(  # noqa: S603
                    [editor, tmp_path],
                    check=False,
                )
                with open(tmp_path, encoding="utf-8") as f:
                    edited_payload = f.read()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            if edited_payload == show["payload"]:
                console.print(
                    f"  [dim]no edits made; approval #{aid} remains queued — "
                    "use /approve or /deny to decide[/dim]",
                )
                continue

            # Submit a new request with the edited payload. The original
            # is denied as superseded. Preserves §10.11 immutability:
            # each (id, hash) pair stays bound to a single payload.
            try:
                new_request = _call(
                    "approval.submit",
                    {
                        "from_session": show.get("from_session"),
                        "action": show.get("action"),
                        "payload": edited_payload,
                        "target": show.get("target"),
                        "labels_in": labels_in,
                        "labels_out": labels_out,
                        "justification": (
                            justification + " (edited from approval " f"#{aid})"
                            if justification
                            else f"edited from approval #{aid}"
                        ),
                    },
                )
                _call(
                    "approval.deny",
                    {"id": aid, "reason": f"superseded by #{new_request.get('id', '?')}"},
                )
                new_id = new_request.get("id", "?")
                console.print(
                    f"  [yellow]edited[/yellow] — approval #{aid} denied as "
                    f"superseded; new approval #{new_id} queued for review",
                )
                # Recurse for the new approval immediately
                if isinstance(new_id, int):
                    _inline_approval_review([new_id])
            except Exception as e:
                err_console.print(f"  [red]edit submit failed:[/red] {e}")
            continue

        if choice == "a":
            result = _call("approval.approve", {"id": aid})
            console.print(f"  [green]✓ approved #{aid}[/green]")
            if result.get("executed_in_session"):
                disp = result.get("dispatch", {})
                if disp.get("error"):
                    console.print(f"    [red]dispatch error:[/red] {disp['error']}")
                else:
                    console.print(
                        f"    dispatched in purpose session "
                        f"{result['executed_in_session'][:8]} "
                        f"({disp.get('decision', '?')})",
                    )
        elif choice == "d":
            _call("approval.deny", {"id": aid, "reason": "denied inline"})
            console.print(f"  [yellow]denied #{aid}[/yellow]")
        else:
            console.print(
                f"  [dim]skipped — still queued; /approve {aid} later[/dim]",
            )


def _repl_loop(session_id: str) -> None:
    # Mutable focus: /switch rebinds these to retarget session.send.
    focus = {"id": session_id, "label": _short_label(session_id)}
    last_result: dict[str, Any] | None = None

    cache = CompletionCache(daemon_call=_call)
    cache.start()

    # Issue #16 — multi-line input. Default to single-line; Alt-Enter
    # (Esc-then-Enter on most terminals) inserts a literal newline so
    # multi-paragraph messages and pasted code blocks work. Enter still
    # submits. Bracketed-paste mode (prompt-toolkit enables it by
    # default when supported) means pasted content with internal
    # newlines is treated as text, not as multiple commands — also a
    # win for safety.
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("escape", "enter")  # Alt-Enter
    def _newline(event) -> None:  # pyright: ignore[reportUnusedFunction]
        event.current_buffer.insert_text("\n")

    pt_session = PromptSession(
        history=FileHistory(str(_history_path())),
        completer=CapDepCompleter(cache),
        complete_while_typing=False,
        bottom_toolbar=_make_bottom_toolbar(cache, focus),
        refresh_interval=1.0,
        multiline=False,  # Enter submits; Alt-Enter inserts newline
        key_bindings=kb,
    )

    console.print(
        f"[bold]chat[/bold] [cyan]{focus['label']}[/cyan] "
        f"[dim]({focus['id'][:8]} · /help · TAB for completion · "
        f"↑/↓ for history · Alt-Enter for newline · live status below)[/dim]",
    )
    try:
        _run_repl(pt_session, focus, last_result)
    finally:
        cache.stop()


def _run_repl(
    pt_session: PromptSession,
    focus: dict[str, str],
    last_result: dict[str, Any] | None,
) -> None:
    while True:
        try:
            line = pt_session.prompt(
                HTML(
                    f"<ansicyan><b>{focus['label']}</b></ansicyan>> ",
                ),
            ).rstrip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return
        if not line:
            continue
        # Issue #12: accept bare `exit` / `quit` / `bye` as REPL exit.
        # Without this they get sent as chat messages and produce
        # confusing "daemon not running" errors if the socket has moved.
        if line.strip().lower() in ("exit", "quit", "bye"):
            return
        if line.startswith("/"):
            cmd, _, arg = line[1:].partition(" ")
            cmd = cmd.lower()
            if cmd in ("quit", "exit"):
                return
            if cmd == "help":
                console.print(_HELP)
                continue
            if cmd == "sessions":
                _handle_sessions()
                continue
            if cmd == "session":
                target = arg.strip() or focus["id"]
                resolved = target if len(target) >= 32 else _resolve_session_id(target)
                if resolved is not None:
                    _handle_session_show(resolved)
                continue
            if cmd == "switch":
                if not arg.strip():
                    err_console.print("[red]usage:[/red] /switch <id>")
                    continue
                resolved = _resolve_session_id(arg.strip())
                if resolved is None:
                    continue
                focus["id"] = resolved
                focus["label"] = _short_label(resolved)
                last_result = None
                console.print(
                    f"[green]→[/green] now talking to "
                    f"[cyan]{focus['label']}[/cyan] [dim]({resolved[:8]})[/dim]",
                )
                continue
            if cmd == "whoami":
                console.print(focus["id"])
                continue
            if cmd == "spawn":
                _handle_spawn(arg, focus)
                last_result = None
                continue
            if cmd == "grant":
                _handle_grant(arg, focus["id"])
                continue
            if cmd == "status":
                _handle_status(focus["id"])
                continue
            if cmd == "labels":
                _handle_status(focus["id"], only="labels")
                continue
            if cmd == "caps":
                _handle_status(focus["id"], only="caps")
                continue
            if cmd == "audit":
                _handle_audit(arg, focus["id"])
                continue
            if cmd == "abort":
                _handle_abort(arg, focus)
                continue
            if cmd == "remember":
                _handle_remember(arg)
                continue
            if cmd == "schemas":
                _handle_schemas()
                continue
            if cmd == "extract":
                _handle_extract(arg)
                continue
            if cmd == "approvals":
                _render_approvals(_list_approvals())
                continue
            if cmd == "approve":
                _handle_approve(arg)
                continue
            if cmd == "deny":
                _handle_deny(arg)
                continue
            if cmd == "submit":
                _handle_submit(focus["id"])
                continue
            if cmd == "trace":
                if last_result is None:
                    console.print("[dim]no turn yet[/dim]")
                else:
                    _render_turn(last_result)
                continue
            if cmd == "tools":
                _handle_tools(arg.strip())
                continue
            if cmd == "override":
                _handle_override(arg, focus["id"])
                continue
            err_console.print(f"[red]unknown command:[/red] /{cmd}")
            continue

        try:
            last_result = _send_message(focus["id"], line)
        except Exception as e:
            err_console.print(f"[red]rpc error:[/red] {e}")
            continue
        _render_turn(last_result)
        # The runtime already registered any REQUIRE_APPROVAL in the
        # queue (at the policy chokepoint). Surface the verbatim review
        # inline, right where it happened — the user doesn't have to
        # remember an id and run a separate command.
        outcomes = last_result.get("tool_outcomes", [])
        pending = [o for o in outcomes if o["decision"] == "require_approval"]
        if pending:
            queued = _pending_approval_ids(pending)
            if queued:
                _inline_approval_review(queued)
            else:
                console.print(
                    "[yellow]→[/yellow] approval required but this tool "
                    "declares no route. use [bold]/submit[/bold] to enqueue "
                    "it manually.",
                )


def chat_command(
    session_id: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Session id to chat with. If omitted, a new session is "
                "created automatically and used."
            ),
        ),
    ] = None,
    intent: Annotated[
        str | None,
        typer.Option(
            "--intent",
            help=(
                "Intent for the auto-created session "
                "(only used when no session_id is given). Default: 'chat'."
            ),
        ),
    ] = None,
    new: Annotated[
        bool,
        typer.Option(
            "--new",
            "-n",
            help=(
                "Force creation of a new session even if a session_id "
                "is passed (the explicit id is ignored)."
            ),
        ),
    ] = False,
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            "-c",
            help=(
                "Daemon config (upstream MCP servers) to use IF the daemon "
                "needs to be auto-started. Ignored when the daemon is "
                "already running."
            ),
        ),
    ] = None,
    no_autostart: Annotated[
        bool,
        typer.Option(
            "--no-autostart",
            help=(
                "Refuse to auto-start the daemon — fail with a clear error "
                "if it's not already running."
            ),
        ),
    ] = False,
    no_default_caps: Annotated[
        bool,
        typer.Option(
            "--no-default-caps",
            help=(
                "When auto-creating a session, do NOT pre-grant the default "
                "read-only capabilities. Operator must /grant explicitly. "
                "(Default: scoped reads on ~/Documents, ~/Projects, "
                "~/Downloads, ~/Desktop, /tmp; CALENDAR_READ; WEB_FETCH; "
                "scratch sandbox. Anything outside requires explicit /grant.)"
            ),
        ),
    ] = False,
) -> None:
    """Interactive REPL against a session.

    Run without arguments to do EVERYTHING:
      - Auto-start the daemon if it isn't already running (background)
      - Auto-create a fresh session
      - Pre-grant default read-only caps (READ_FS, CALENDAR_READ,
        WEB_FETCH wildcards) so the agent can actually do work
      - Drop into the chat REPL

      capdep chat                       # one-command experience
      capdep chat --config <path.yaml>  # auto-start daemon with this config
      capdep chat <session-id>          # resume existing session (no auto-grant)
      capdep chat --new                 # force fresh session
      capdep chat --intent "X"          # custom intent for new session
      capdep chat --no-autostart        # fail if daemon not running
      capdep chat --no-default-caps     # don't auto-grant; use /grant manually

    The chokepoint still enforces every label / Brewer-Nash / expiry /
    rate rule — the default caps just mean "kinds of action allowed
    for this session" without per-call /grant friction.
    """
    _ensure_daemon(autostart=not no_autostart, config=config)

    effective_id: str | None = None if new else session_id
    auto_created = False
    if effective_id is None:
        params: dict[str, Any] = {"intent": intent or "chat"}
        s = _call("session.new", params)
        effective_id = str(s["id"])
        auto_created = True
        console.print(
            f"[green]new session:[/green] {effective_id}  intent={intent or 'chat'}",
        )

    # Pre-grant useful read-only caps on auto-created sessions so the
    # agent doesn't fail at "I have no tools available" — the user
    # shouldn't have to chant /grant for safe reads.
    if auto_created and not no_default_caps:
        _grant_default_read_caps(effective_id)

    _repl_loop(effective_id)


def _grant_default_read_caps(session_id: str) -> None:
    """Grant the safe, read-only capability set every personal-assistant
    session needs: filesystem reads, calendar reads, web fetches, and
    common modify-style operations.

    The chokepoint still enforces label propagation, Brewer-Nash, and
    every other rule on top — these caps only mean "this kind of
    action is generally permitted for this session", not "let
    everything through".
    """
    from uuid import uuid4

    # Issue #6 — Scope READ_FS away from system files. Previously this
    # was `READ_FS *` which let the agent read /etc/passwd, ~/.ssh/*,
    # ~/.aws/*, etc. The scoped set covers normal work dirs; the agent
    # can still `/grant READ_FS <path>` for anything outside.
    #
    # ~ expansion is intentionally NOT done here — the daemon's pattern
    # matcher does shell-style globbing and `~` is fine as a literal.
    # If your home isn't /home/<you>, edit your auto-grant via /grant.
    import os as _os

    home = _os.path.expanduser("~")
    default_caps = (
        ("READ_FS", f"{home}/Documents/*"),
        ("READ_FS", f"{home}/Projects/*"),
        ("READ_FS", f"{home}/Downloads/*"),
        ("READ_FS", f"{home}/Desktop/*"),
        ("READ_FS", "/tmp/*"),
        ("CALENDAR_READ", "*"),
        ("WEB_FETCH", "*"),
        ("CREATE_FS", f"{home}/.capdep/work/*"),
        ("CREATE_FS", "/tmp/*"),
        # Sandbox: granting scoped to the bundled `scratch` region.
        # If the daemon has no actuator wired, sandbox.run won't be in
        # the tool list anyway, so the cap is harmless.
        ("EXECUTE_SANDBOX", "scratch"),
    )
    granted: list[str] = []
    for kind, pattern in default_caps:
        cap = {
            "kind": kind,
            "pattern": pattern,
            "expiry": "session",
            "origin": "user_approved",
            "audit_id": str(uuid4()),
            "allows_destructive": False,
            "revoked_by": [],
            "expires_at": None,
            "rate_limit": None,
        }
        try:
            _call(
                "session.grant_capability",
                {"session_id": session_id, "capability": cap},
            )
            granted.append(f"{kind}({pattern})")
        except Exception as e:
            err_console.print(
                f"[yellow]could not pre-grant {kind}: {e}[/yellow]",
            )
    if granted:
        console.print(
            f"[dim]pre-granted caps: {', '.join(granted)}  "
            "(use [bold]/grant[/bold] for more; "
            "[bold]--no-default-caps[/bold] to disable)[/dim]",
        )


demo_app = typer.Typer(
    help="Interactive demo scenarios with seeded stubbed tools.",
    no_args_is_help=True,
)


@demo_app.command("list")
def demo_list() -> None:
    """List available built-in demo scenarios."""
    _ensure_daemon()
    result = _call("demo.list_scenarios")
    table = Table(title="Available demo scenarios")
    table.add_column("Name")
    table.add_column("Summary")
    for s in result["scenarios"]:
        table.add_row(s["name"], s["one_line"])
    console.print(table)
    console.print("\nrun [bold]capdep demo start <name>[/bold] to begin.")


@demo_app.command("start")
def demo_start(
    name: Annotated[str, typer.Argument(help="Scenario name")],
    no_chat: Annotated[
        bool,
        typer.Option(
            "--no-chat",
            help="Seed the scenario and print the session id, but skip the REPL.",
        ),
    ] = False,
) -> None:
    """Seed a scenario's data and (by default) drop into the chat REPL."""
    _ensure_daemon()
    result = _call("demo.start", {"name": name})
    if "error" in result:
        err_console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(code=1)

    scenario = result["scenario"]
    counts = result["seed_counts"]
    session_id = result["session_id"]

    console.print(
        Panel(
            scenario["intro"],
            title=f"scenario: {scenario['name']}",
            border_style="cyan",
        ),
    )
    console.print(
        f"[dim]seeded:[/dim] inbox={counts['inbox']} calendar={counts['calendar']} "
        f"memory={counts['memory']} capabilities={counts['capabilities']}",
    )
    console.print(f"[dim]session:[/dim] {session_id}")
    if scenario["suggested_prompts"]:
        console.print("[bold]try asking:[/bold]")
        for p in scenario["suggested_prompts"]:
            console.print(f"  - {p}")
    if scenario["security_note"]:
        console.print(
            Panel(scenario["security_note"], title="security note", border_style="yellow"),
        )

    if no_chat:
        console.print(
            f"\nstart chatting with [bold]capdep chat {session_id}[/bold]",
        )
        return

    sys.stdout.flush()
    _repl_loop(session_id)
