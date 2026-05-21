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


def _ensure_daemon() -> None:
    try:
        _call("ping")
    except DaemonNotRunningError:
        err_console.print(
            "[red]daemon not running.[/red] start it in another terminal "
            "with [bold]capdep daemon start[/bold] and re-run this command.",
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
            hint = _DENY_RECOVERY.get(o.get("rule") or "")
            if hint:
                console.print(f"  [cyan]↳ recover:[/cyan] [dim]{hint}[/dim]")
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
            hint = _DENY_RECOVERY.get(rule)
            if hint:
                console.print(f"  [cyan]↳ recover:[/cyan] [dim]{hint}[/dim]")
        if o.get("error"):
            console.print(f"  [red]error:[/red] {o['error']}")


def _render_turn(result: dict[str, Any]) -> None:
    _TURN_COUNTER["n"] += 1
    console.rule(
        f"[dim]turn {_TURN_COUNTER['n']} · "
        f"iters={result['iterations']} · {result['finish_reason']}[/dim]",
        align="left",
        style="dim",
    )
    console.print(f"[bold cyan]agent[/bold cyan]  {result['content']}")
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

  declassify: /schemas               — list declassification schemas
              /extract <msg> <schema> — quarantined-LLM extraction

  trace:   /trace  /audit [N] [--full]

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
    """/remember <ACTION> <target-glob>  — install an auto-approval
    pattern. Example: /remember QUEUE_PURCHASE amazon.com
    """
    parts = arg.split()
    if len(parts) < 2:
        err_console.print(
            "[red]usage:[/red] /remember <ACTION> <target-pattern> "
            "(e.g. /remember QUEUE_PURCHASE amazon*)",
        )
        return
    action, target_pattern = parts[0].upper(), parts[1]
    try:
        result = _call(
            "pattern.create",
            {
                "action": action,
                "target_pattern": target_pattern,
                "max_amount": None,
            },
        )
    except Exception as e:
        err_console.print(f"[red]pattern.create failed:[/red] {e}")
        return
    pattern = result.get("pattern") or result
    console.print(
        f"[green]✓ pattern[/green] {action} {target_pattern} → id={pattern.get('id', '?')}",
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
    """The crown-jewel human-in-the-loop step, surfaced where it
    happens. For each runtime-queued approval: render the verbatim
    payload and prompt [a]pprove / [d]eny / [s]kip inline — no need to
    remember the id and run a separate /approve."""
    for aid in approval_ids:
        try:
            show = _call("approval.show", {"id": aid})
        except Exception as e:
            err_console.print(f"[red]could not load approval #{aid}:[/red] {e}")
            continue
        console.print(
            Panel(
                show["payload"],
                title=(f"approval #{aid} · {show['action']} → {show['target']}"),
                subtitle="[dim]verbatim — this is exactly what will happen[/dim]",
                border_style="yellow",
            ),
        )
        choice = (
            Prompt.ask(
                f"  approval #{aid}",
                choices=["a", "d", "s"],
                default="s",
            )
            .strip()
            .lower()
        )
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
    pt_session = PromptSession(
        history=FileHistory(str(_history_path())),
        completer=CapDepCompleter(cache),
        complete_while_typing=False,
        bottom_toolbar=_make_bottom_toolbar(cache, focus),
        refresh_interval=1.0,
    )

    console.print(
        f"[bold]chat[/bold] [cyan]{focus['label']}[/cyan] "
        f"[dim]({focus['id'][:8]} · /help · TAB for completion · "
        f"↑/↓ for history · live status below)[/dim]",
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
) -> None:
    """Interactive REPL against a session.

    Run without arguments to auto-create a fresh session and drop
    straight into chat:

      capdep chat                  # creates session, enters REPL
      capdep chat <session-id>     # uses existing session
      capdep chat --new            # forces a new session
      capdep chat --intent "X"     # set intent for the auto-created session
    """
    _ensure_daemon()

    effective_id: str | None = None if new else session_id
    if effective_id is None:
        params: dict[str, Any] = {"intent": intent or "chat"}
        s = _call("session.new", params)
        effective_id = str(s["id"])
        console.print(
            f"[green]new session:[/green] {effective_id}  intent={intent or 'chat'}",
        )

    _repl_loop(effective_id)


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
