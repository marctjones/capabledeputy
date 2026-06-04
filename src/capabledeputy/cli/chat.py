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
    /enforce shadow|strict  flip enforcement (Pattern ⑥ shadow mode)
    /first-use on|off       toggle first-action-of-kind prompt

  Approvals:
    /approvals              list pending approvals (grouped siblings shown together)
    /approve <id>           verbatim payload → y/N → approve
    /approve-group <gid>    approve every sibling in a group at once
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

import contextlib
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


# ---- visual palette ---------------------------------------------------
# Centralized so every render path uses the same vocabulary. Before
# this, surfaces sprinkled cyan/bold/dim ad-hoc and the result read
# "developer terminal" instead of "polished product." Three brand
# tokens (assistant / user / dim) plus the three semantic tokens
# (error / success / warning). Everything else either uses these or
# `[dim]` for de-emphasized prose.
#
# `orange3` is a 256-color name that maps to a warm amber on Rich's
# default theme — close enough to Claude's brand color on truecolor
# terminals (Ghostty/kitty/iTerm2) while degrading cleanly on basic
# xterms that only have the 256-color palette.
ASSISTANT_COLOR = "orange3"
USER_COLOR = "cyan"
DIM_COLOR = "dim"
ERROR_COLOR = "red"
SUCCESS_COLOR = "green"
WARNING_COLOR = "yellow"

# Speaker glyph — the colored `●` that prefixes each turn header.
# Claude Code, Cursor, Aider, and friends all use a colored dot
# convention; it's the single highest-signal "this app feels like
# Claude Code" cue and costs one character per turn.
SPEAKER_GLYPH = "●"


def _speaker_line(
    name: str,
    color: str,
    *,
    timestamp: str | None = None,
    suffix: str | None = None,
) -> str:
    """Format the `●  name  HH:MM  · extra` header line. The dot
    carries the speaker's brand color, the name is bold, and
    timestamp/suffix render dim so the eye lands on the colored dot
    first. Returns a Rich-markup string ready for `console.print`."""
    parts = [f"[{color}]{SPEAKER_GLYPH}[/{color}] [bold]{name}[/bold]"]
    if timestamp:
        parts.append(f"[dim]{timestamp}[/dim]")
    if suffix:
        parts.append(f"[dim]{suffix}[/dim]")
    return "  ".join(parts)


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
        IMAP_BLOCK_ID,
        has_managed_block,
        imap_credentials_present,
        resolve_daemon_config_with_source,
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
        label = "user default" if source == "user-default" else source
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
# Per-process holder for the operator's `--max-iters` choice.
# Read on every _send_message call so the daemon honors it.
_SESSION_MAX_ITERS: dict[str, int] = {"value": 50}


_TOOL_ICONS: tuple[tuple[str, str], ...] = (
    # Ordered prefix→icon map. Longer prefixes first so `gws.gmail_*`
    # wins over `gws.*`. The point isn't taxonomic correctness — it's
    # that the icon gives a one-glance read on what kind of work the
    # agent just did.
    ("gws.gmail", "📧"),
    ("gws.drive", "📂"),
    ("gws.calendar", "📅"),
    ("gws.docs", "📄"),
    ("gws.sheets", "📊"),
    ("mail.", "📧"),
    ("email.", "✉️"),
    ("calendar.", "📅"),
    ("fs.", "📁"),
    ("filesystem.", "📁"),
    ("fetch.", "🌐"),
    ("bundled-fetch", "🌐"),
    ("web.", "🌐"),
    ("bundled-search", "🔎"),
    ("search.", "🔎"),
    ("extract.", "🔎"),
    ("git.", "🔱"),
    ("bundled-git", "🔱"),
    ("memory.", "🧠"),
    ("inbox.", "📥"),
    ("approval.", "⚖️"),
    ("policy.", "⚖️"),
    ("sandbox.", "📦"),
    ("tasks.", "✅"),
    ("purchase.", "💳"),
)


def _tool_icon(tool_name: str | None) -> str:
    """Map a tool name to a one-character/emoji icon. Default `🔧`
    when no prefix matches — at-a-glance signal that a tool ran,
    even if we don't know its category."""
    if not tool_name:
        return "🔧"
    for prefix, icon in _TOOL_ICONS:
        if tool_name.startswith(prefix):
            return icon
    return "🔧"


def _summarize_tool_args(args: Any) -> str:
    """Compact `(k=v, k=v)` summary of tool args. Picks the
    most-meaningful keys (q/path/url/id) first, truncates long
    values, and caps total length so a multi-tool turn stays on one
    line per call. Empty when args are absent or unparseable."""
    if not args or not isinstance(args, dict):
        return ""
    priority = ("q", "query", "path", "url", "id", "key", "name", "subject", "to", "spec_id")
    seen: set[str] = set()
    chosen: list[tuple[str, Any]] = []
    for k in priority:
        if k in args:
            chosen.append((k, args[k]))
            seen.add(k)
        if len(chosen) >= 2:
            break
    if len(chosen) < 2:
        for k in args:
            if k not in seen:
                chosen.append((k, args[k]))
                if len(chosen) >= 2:
                    break
    parts: list[str] = []
    for k, v in chosen:
        vs = str(v).replace("\n", " ")
        if len(vs) > 24:
            vs = vs[:21] + "…"
        parts.append(f"{k}={vs}")
    return ", ".join(parts)


def _parse_json_text(output: Any) -> Any:
    """Upstream MCP tools wrap structured payloads as
    `{"text": "<json blob>"}`. Try to parse the inner JSON so
    per-tool formatters can read fields. Returns None when output
    isn't a dict, isn't text-shaped, or the inner blob isn't valid
    JSON. Cheap to call — formatters that don't need the parsed
    form just won't invoke this."""
    import json

    if not isinstance(output, dict):
        return None
    text = output.get("text")
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _fmt_gmail_messages_get(output: Any) -> str | None:
    """Gmail message → `From: <sender> · Subject: <subject>`.

    Pulls the canonical headers (From, Subject) out of the
    `payload.headers` array. Common case: a substack newsletter
    renders as `From: The Capitalist · "Legendary automaker..."`,
    which is much more useful than a 30k-char byte count."""
    parsed = _parse_json_text(output)
    if not isinstance(parsed, dict):
        return None
    headers = (parsed.get("payload") or {}).get("headers") or []
    if not isinstance(headers, list):
        return None
    h: dict[str, str] = {}
    for entry in headers:
        if isinstance(entry, dict):
            name = entry.get("name", "")
            value = entry.get("value", "")
            if isinstance(name, str) and isinstance(value, str):
                h[name.lower()] = value
    sender_raw = h.get("from") or "?"
    sender = sender_raw.split("<")[0].strip().strip('"') or sender_raw[:40]
    if len(sender) > 30:
        sender = sender[:27] + "…"
    subj = h.get("subject") or "(no subject)"
    if len(subj) > 50:
        subj = subj[:47] + "…"
    return f'[dim]From: {sender} · "{subj}"[/dim]'


def _fmt_gmail_messages_list(output: Any) -> str | None:
    """Gmail thread/message list → `N messages`. The bare list
    response carries only ids + threadIds (no subjects), so we
    can't do better without a follow-up fetch. Keep the count and
    let the agent supply the narrative."""
    parsed = _parse_json_text(output)
    if not isinstance(parsed, dict):
        return None
    msgs = parsed.get("messages")
    if isinstance(msgs, list):
        return f"[dim]{len(msgs)} messages[/dim]"
    return None


def _fmt_drive_files_list(output: Any) -> str | None:
    """Drive file list → `3 files: report.pdf, notes.md, slides.key…`."""
    parsed = _parse_json_text(output)
    if not isinstance(parsed, dict):
        return None
    files = parsed.get("files")
    if not isinstance(files, list):
        return None
    names: list[str] = []
    for f in files[:3]:
        if isinstance(f, dict):
            n = f.get("name")
            if isinstance(n, str):
                names.append(n)
    if not names:
        return f"[dim]{len(files)} files[/dim]"
    suffix = ", …" if len(files) > 3 else ""
    joined = ", ".join(names)
    if len(joined) > 60:
        joined = joined[:57] + "…"
    return f"[dim]{len(files)} files: {joined}{suffix}[/dim]"


def _fmt_fs_read(output: Any) -> str | None:
    """File read → `<N> lines · <bytes>`. Counts newlines in the
    returned content; bytes shown from explicit length or string len."""
    if not isinstance(output, dict):
        return None
    content = output.get("content")
    if not isinstance(content, str):
        content = output.get("text")
    if not isinstance(content, str):
        return None
    n_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    n_bytes = len(content.encode("utf-8"))
    if output.get("truncated"):
        return f"[dim]{n_lines} lines · {n_bytes:,} bytes (truncated)[/dim]"
    return f"[dim]{n_lines} lines · {n_bytes:,} bytes[/dim]"


def _fmt_fetch(output: Any) -> str | None:
    """HTTP fetch → `<status> · <bytes>`. Pulls status + body length
    from the canonical {status, body, url} shape produced by the
    bundled fetch server."""
    if not isinstance(output, dict):
        return None
    status = output.get("status")
    body = output.get("body") or output.get("text")
    if status is None and not isinstance(body, str):
        return None
    parts: list[str] = []
    if status is not None:
        parts.append(str(status))
    if isinstance(body, str):
        parts.append(f"{len(body):,} bytes")
    return f"[dim]{' · '.join(parts)}[/dim]" if parts else None


# Per-tool result formatters. Keyed by exact tool name. Formatters
# return None when they can't extract a useful preview, in which
# case `_summarize_tool_output` falls through to the generic
# byte-count/item-count rendering. The point: a 30k-char gmail
# message renders as `From: ... · "Subject..."` not
# `30,054 chars`, which is what a human actually wants to scan.
_TOOL_RESULT_FORMATTERS: dict[str, Any] = {
    "gws.gmail_messages_get": _fmt_gmail_messages_get,
    "gws.gmail_messages_list": _fmt_gmail_messages_list,
    "gws.drive_files_list": _fmt_drive_files_list,
    "fs.read": _fmt_fs_read,
    "bundled-fetch.fetch": _fmt_fetch,
    "fetch.fetch": _fmt_fetch,
}


def _summarize_tool_output(outcome: dict[str, Any]) -> str:
    """One-line preview of what the tool returned. Order of attempts:

    1. Decision short-circuits (deny / require_approval / error).
    2. Per-tool formatter from `_TOOL_RESULT_FORMATTERS` — pulls
       domain-specific fields (gmail Subject, drive filenames,
       fs line count, fetch status).
    3. Truncation marker if the upstream adapter capped the output.
    4. Generic shape-based fallback (byte count for text, item count
       for list-keyed dicts, field count as a last resort).

    Always returns Rich markup ready for `console.print` (may
    include color tags for errors/denials)."""
    decision = outcome.get("decision")
    if decision == "deny":
        rule = outcome.get("rule") or "policy"
        return f"[{ERROR_COLOR}]DENIED by {rule}[/{ERROR_COLOR}]"
    if decision == "require_approval":
        return f"[{WARNING_COLOR}]queued for approval[/{WARNING_COLOR}]"
    if outcome.get("error"):
        err = str(outcome["error"]).replace("\n", " ")
        if len(err) > 60:
            err = err[:57] + "…"
        return f"[{ERROR_COLOR}]✗ {err}[/{ERROR_COLOR}]"
    output = outcome.get("output")
    if output is None:
        return ""
    # Per-tool formatter wins when applicable. Try the exact name
    # first; formatters return None to fall through to the generic
    # path.
    tool_name = outcome.get("tool_name") or ""
    formatter = _TOOL_RESULT_FORMATTERS.get(tool_name)
    if formatter is not None:
        try:
            specific = formatter(output)
        except Exception:
            specific = None  # bad data → fall through, never crash render
        if specific:
            # Decorate with the truncation marker if the upstream
            # output was capped — the formatter sees the truncated
            # content but the operator should know there was more.
            if isinstance(output, dict) and output.get("truncated"):
                orig = output.get("original_size_bytes", 0)
                return f"{specific} [dim](truncated · {orig:,} bytes total)[/dim]"
            return specific

    if isinstance(output, dict):
        if output.get("upstream_error"):
            text = str(output.get("text", "")).replace("\n", " ")[:60]
            return f"[{ERROR_COLOR}]upstream error: {text}[/{ERROR_COLOR}]"
        if output.get("truncated"):
            orig = output.get("original_size_bytes", 0)
            return f"[dim]{orig:,} bytes (truncated)[/dim]"
        if "text" in output and isinstance(output["text"], str):
            n = len(output["text"])
            return f"[dim]{n:,} chars[/dim]"
        for k in ("messages", "items", "results", "files", "events", "rows", "approvals"):
            v = output.get(k)
            if isinstance(v, list):
                return f"[dim]{len(v)} {k}[/dim]"
        if output:
            return f"[dim]{len(output)} fields[/dim]"
        return ""
    if isinstance(output, list):
        return f"[dim]{len(output)} items[/dim]"
    s = str(output).replace("\n", " ")
    if len(s) > 60:
        s = s[:57] + "…"
    return f"[dim]{s}[/dim]"


def _render_outcomes_table(outcomes: list[dict[str, Any]]) -> None:
    """Per-tool-call inline cards (Claude-Code-style) instead of a
    full Rich Table. Each call renders as:

        📧 gws.gmail_messages_list(q=after:..., maxResults=20)
                 → 8 messages

    where the icon comes from the tool prefix, the args are an
    abbreviated `(k=v, k=v)` summary, and the result preview is a
    byte count / item count / denial rule. Denials render with `⊘`
    in red instead of the tool icon, so they pop visually in a
    sea of allows.

    DENY/approval reasons + recovery hints render below the card
    where they won't be missed."""
    if not outcomes:
        return
    for o in outcomes:
        decision = o.get("decision", "")
        tool_name = o.get("tool_name") or "?"
        args_summary = _summarize_tool_args(o.get("tool_args"))
        output_summary = _summarize_tool_output(o)

        if decision == "deny":
            icon = f"[{ERROR_COLOR}]⊘[/{ERROR_COLOR}]"
        elif decision == "require_approval":
            icon = f"[{WARNING_COLOR}]⚠[/{WARNING_COLOR}]"
        else:
            icon = _tool_icon(tool_name)

        call_str = (
            f"[bold]{tool_name}[/bold]([dim]{args_summary}[/dim])"
            if args_summary
            else f"[bold]{tool_name}[/bold]"
        )
        line = f"  {icon} {call_str}"
        if output_summary:
            line += f" [dim]→[/dim] {output_summary}"
        # Labels-added is rare-but-useful; render inline when present
        # rather than burying it in a "rule / labels" column.
        if o.get("labels_added"):
            added = " ".join(
                f"[{_label_rich_style(lbl)}]+{lbl}[/{_label_rich_style(lbl)}]"
                for lbl in o["labels_added"]
            )
            line += f"  {added}"
        console.print(line)

    # Per-outcome detail lines below the cards: reasons + actionable
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
            rationale = s.get("rationale") if isinstance(s, dict) else getattr(s, "rationale", "")
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


def _render_user_message(message: str) -> None:
    """Echo the user's input back into scrollback as a chat-style
    message. Without this, the user's typed message vanishes after
    Enter — prompt-toolkit clears the prompt line — and the
    conversation reads as one-sided in scrollback.

    Style: `● you  HH:MM` with a cyan dot, matching the agent's
    `●` header so the conversation reads as a turn-taking transcript.
    No border, no panel — just the speaker prefix and the message."""
    from datetime import datetime

    ts = datetime.now().strftime("%H:%M")
    console.print()
    console.print(_speaker_line("you", USER_COLOR, timestamp=ts))
    console.print(message)


def _render_turn(result: dict[str, Any]) -> None:
    """Render the agent's turn. Chat-style: `● agent  turn N  · 3 iters`
    header, compact tool-call summaries, then the agent's markdown
    response. No panels, no thick borders — flows like a chat
    transcript.

    Tool outcomes render BEFORE the agent's prose (#30) so the
    chronology of "agent called tools → agent composed response" is
    preserved when reading top-to-bottom."""
    _TURN_COUNTER["n"] += 1
    iters = result.get("iterations", 1)
    finish = result.get("finish_reason", "stop")
    n_tools = len(result.get("tool_outcomes", []) or [])

    # Compose the dim suffix: `turn N · 3 iters · 2 tool calls · stop`.
    # Order picked so the most session-stable bits come first.
    suffix_bits = [f"turn {_TURN_COUNTER['n']}"]
    if iters > 1:
        suffix_bits.append(f"{iters} iters")
    if n_tools:
        suffix_bits.append(f"{n_tools} tool call{'s' if n_tools != 1 else ''}")
    if finish != "stop":
        suffix_bits.append(finish)
    suffix = " · ".join(suffix_bits)

    console.print()
    console.print(_speaker_line("agent", ASSISTANT_COLOR, suffix=suffix))

    # Issue #30 — tool outcomes before agent prose so reading
    # top-to-bottom preserves chronology.
    _render_outcomes_table(result.get("tool_outcomes", []))

    # Issue #16 track 1 + terminal-quality polish: render agent
    # output as markdown with capability-aware settings.
    content = result["content"]
    console.print(_render_markdown(content))


def _render_markdown(content: str):
    """Build a Rich Markdown renderer with settings tuned for the
    operator's terminal. Modern terminals (Ghostty, kitty, iTerm2,
    WezTerm, Alacritty) get a truecolor-rich code theme + explicit
    hyperlinks; basic terminals get the conservative monokai fallback.

    Returns a `rich.markdown.Markdown` object ready to console.print().

    What this actually buys you (in Ghostty specifically):
    - **Code theme**: switches from 256-color monokai to a truecolor
      theme (`one-dark`) that uses the full 24-bit gamut. Visible on
      syntax-highlighted code blocks.
    - **Inline code highlighting**: enabled by default in Rich for
      ```python (etc.) but inline `code` was unstyled. Now uses the
      same theme.
    - **Hyperlinks**: forced ON. The default is auto-detect, which
      Rich sometimes downgrades on stdout buffers. Ghostty supports
      OSC 8 (per terminal_caps detection); now we always emit it.
    - **Tables**: not directly controlled here — Rich's markdown
      table renderer uses console width auto-detect. Already good
      on Ghostty.

    What it DOESN'T do (intentional):
    - Render markdown image links as inline graphics via kitty
      protocol. Possible (Ghostty supports it), but the agent
      rarely produces image-bearing markdown today. Filed as a
      follow-on under #19's scope.
    """
    from rich.markdown import Markdown

    from capabledeputy.cli.terminal_caps import caps as _caps

    c = _caps()
    # Modern truecolor families get a richer code theme. Both
    # "one-dark" and "monokai" exist in pygments; one-dark looks
    # better on Ghostty/kitty/iterm where truecolor + good font
    # rendering shine.
    if c.truecolor and c.family in ("ghostty", "kitty", "iterm2", "wezterm", "alacritty"):
        code_theme = "one-dark"
    else:
        code_theme = "monokai"

    return Markdown(
        content,
        code_theme=code_theme,
        hyperlinks=c.hyperlinks,
        inline_code_lexer="python",  # best-guess for inline `code` spans
        inline_code_theme=code_theme,
    )


def _list_approvals(status: str = "pending") -> list[dict[str, Any]]:
    result = _call("approval.list", {"status": status})
    return result["approvals"]


def _render_approvals(approvals: list[dict[str, Any]]) -> None:
    """Three-section view: first-use banners first (each FIRST_USE
    approval gets its own ⚠ banner with friendlier copy because
    it asks a different question — "intend to ever do this in
    this session?" — than rule-driven gates), then grouped
    siblings, then solo approvals as the regular table.

    The sibling group is the cookbook P2.1 mechanism: N requests
    sharing (session, action, target) within a 5-second window get
    a shared `sibling_group_id`. The operator can approve all at
    once via `/approve-group <gid>` (paste link in the group
    header), or use `/approve <id>` / `/deny <id>` on individual
    members."""
    if not approvals:
        console.print("[dim]no pending approvals[/dim]")
        return

    # Roadmap v2 #7 — split first-use prompts (cookbook §4 #6) out
    # of the standard queue display. They look like ordinary
    # REQUIRE_APPROVAL on the wire but ask a fundamentally
    # different question of the operator, so render them with a
    # distinct banner above the standard table.
    first_use: list[dict[str, Any]] = []
    standard: list[dict[str, Any]] = []
    for a in approvals:
        if a.get("rule") == "first-use-of-kind":
            first_use.append(a)
        else:
            standard.append(a)
    for a in first_use:
        clickable_id = _paste_link(str(a["id"]), f"/approve {a['id']}")
        deny_id = _paste_link("deny", f"/deny {a['id']}")
        console.print(
            f"[yellow]⚠ first use of [bold]{a['action']}[/bold] in this "
            f"session[/yellow] — confirm intent",
        )
        console.print(
            f"  target: [dim]{a['target']}[/dim]  "
            f"approval {clickable_id}  ·  {deny_id}",
        )
        console.print(
            "  [dim]This kind of capability hasn't been exercised yet. "
            "Approving teaches the session this is intended; subsequent "
            "uses won't re-prompt unless a policy rule fires.[/dim]",
        )

    # Split standard requests into groups vs solos.
    groups: dict[str, list[dict[str, Any]]] = {}
    solos: list[dict[str, Any]] = []
    for a in standard:
        gid = a.get("sibling_group_id")
        if gid:
            groups.setdefault(gid, []).append(a)
        else:
            solos.append(a)
    # A "group" with one member is conceptually a solo — promote it.
    for gid, members in list(groups.items()):
        if len(members) == 1:
            solos.append(members[0])
            del groups[gid]

    for gid, members in groups.items():
        action = members[0]["action"]
        target = members[0]["target"]
        approve_group_link = _paste_link(
            f"approve-all ({len(members)})",
            f"/approve-group {gid}",
        )
        title = (
            f"[bold]Sibling group[/bold] {action} → {target}  "
            f"· {len(members)} pending  · {approve_group_link}"
        )
        gtable = Table(title=title, show_header=True)
        gtable.add_column("ID")
        gtable.add_column("Payload preview")
        for m in members:
            preview = m["payload"][:60]
            if len(m["payload"]) > 60:
                preview += "…"
            clickable_id = _paste_link(str(m["id"]), f"/approve {m['id']}")
            gtable.add_row(clickable_id, preview)
        console.print(gtable)

    if solos:
        table = Table(title=f"Pending approvals ({len(solos)} solo)")
        table.add_column("ID")
        table.add_column("Action")
        table.add_column("Target")
        table.add_column("Payload preview")
        for a in solos:
            preview = a["payload"][:60]
            if len(a["payload"]) > 60:
                preview += "…"
            clickable_id = _paste_link(str(a["id"]), f"/approve {a['id']}")
            table.add_row(clickable_id, a["action"], a["target"], preview)
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
    # Cookbook P2.3 — auto-narrowing. Offer to add the approved
    # counterparty to a relationship group so the next send to the
    # same identity surfaces as a recognized counterparty.
    _offer_relationship_capture(show.get("action"), show.get("target"))


def _offer_relationship_capture(action: str | None, target: str | None) -> None:
    """If the approved action is send-shaped and target is a real
    identity, prompt the operator to add it to a relationship group.
    Skips when relationship_groups isn't wired or the target is
    already in a group."""
    if not action or not target:
        return
    if action not in ("send_email", "queue_purchase", "execute_destructive"):
        return
    try:
        groups_resp = _call("relationship_group.list")
    except Exception:
        # No relationship_groups wired or daemon mismatch — silently
        # skip. The auto-narrowing is a convenience, not a guarantee.
        return
    groups = groups_resp.get("groups", [])
    # Check whether target is already a member of any group.
    already_in = sorted(
        g["group_id"] for g in groups if target in g.get("member_principal_ids", [])
    )
    if already_in:
        console.print(
            f"  [dim]{target} already in: {', '.join(already_in)}[/dim]",
        )
        return
    if not groups:
        console.print(
            f"  [dim]no relationship groups declared yet.[/dim] "
            f"[dim]Add `{target}` to a new group? [y/N][/dim]",
            end=" ",
        )
        confirm = Prompt.ask("", default="N").strip().lower()
        if confirm not in ("y", "yes"):
            return
        new_group = Prompt.ask("  new group_id").strip()
        if not new_group:
            return
        _call_add_member(new_group, target)
        return
    # Build the picker: numbered group list + n for new + s to skip.
    console.print(
        f"  [bold]Remember[/bold] [cyan]{target}[/cyan] in a relationship group?",
    )
    for i, g in enumerate(groups, start=1):
        console.print(
            f"    {i}. {g['group_id']} "
            f"[dim]({len(g.get('member_principal_ids', []))} members)[/dim]",
        )
    console.print(
        "    n. new group   s. skip",
    )
    pick = Prompt.ask("  pick", default="s").strip().lower()
    if pick in ("s", "skip", ""):
        return
    if pick in ("n", "new"):
        new_group = Prompt.ask("  new group_id").strip()
        if not new_group:
            return
        _call_add_member(new_group, target)
        return
    try:
        idx = int(pick)
    except ValueError:
        console.print(f"  [yellow]not a choice:[/yellow] {pick!r}")
        return
    if not (1 <= idx <= len(groups)):
        console.print(f"  [yellow]out of range:[/yellow] {pick!r}")
        return
    _call_add_member(groups[idx - 1]["group_id"], target)


def _call_add_member(group_id: str, principal_id: str) -> None:
    try:
        result = _call(
            "relationship_group.add_member",
            {"group_id": group_id, "principal_id": principal_id},
        )
    except Exception as e:
        err_console.print(f"  [red]add_member failed:[/red] {e}")
        return
    if result.get("added"):
        if result.get("persisted"):
            console.print(
                f"  [green]✓ added[/green] {principal_id} → [bold]{group_id}[/bold] (persisted)",
            )
        else:
            err = result.get("persist_error", "unknown")
            console.print(
                f"  [yellow]added in-memory but persist failed:[/yellow] {err}",
            )
    else:
        console.print(
            f"  [dim]already member of {group_id}[/dim]",
        )


def _handle_approve_group(arg: str) -> None:
    """`/approve-group <gid>` — approve every PENDING sibling in the
    given group. Shows the full per-member preview first so the
    operator can confirm what's about to fire as a batch.

    Already-decided members are skipped (the operator may have
    denied one individually first). Failures on individual sends
    don't roll back the others; each result is surfaced inline."""
    gid = arg.strip()
    if not gid:
        err_console.print(
            "[red]usage:[/red] /approve-group <gid> "
            "— paste from the [bold]approve-all (N)[/bold] link in /approvals",
        )
        return
    pending = _list_approvals()
    members = [p for p in pending if p.get("sibling_group_id") == gid]
    if not members:
        err_console.print(
            f"[red]no pending siblings in group[/red] {gid}",
        )
        return
    # Preview every member before firing.
    console.print(
        Panel(
            "\n\n".join(
                f"[bold]#{m['id']}[/bold] {m['action']} → {m['target']}\n{m['payload']}"
                for m in members
            ),
            title=f"approve-group {gid} — {len(members)} sibling(s)",
            border_style="yellow",
        ),
    )
    confirm = Prompt.ask(f"approve all {len(members)}? [y/N]", default="N").strip().lower()
    if confirm not in ("y", "yes"):
        console.print("[dim]not approving[/dim]")
        return
    result = _call("approval.approve_group", {"group_id": gid})
    n_ok = result.get("n_approved", 0)
    n_skip = result.get("n_skipped", 0)
    n_fail = result.get("n_failed", 0)
    console.print(
        f"[green]✓ approved {n_ok}[/green]"
        + (f"  [dim]{n_skip} skipped (already decided)[/dim]" if n_skip else "")
        + (f"  [red]{n_fail} failed[/red]" if n_fail else ""),
    )
    # Per-member dispatch summary so failures don't get lost.
    for r in result.get("results", []):
        if r.get("error"):
            console.print(
                f"  [red]#{r.get('id')} error:[/red] {r['error']}",
            )
        elif r.get("skipped"):
            console.print(
                f"  [dim]#{r.get('id')} skipped:[/dim] {r.get('reason')}",
            )
        elif r.get("executed_in_session"):
            sid = str(r["executed_in_session"])[:8]
            console.print(
                f"  [green]#{r.get('id')}[/green] dispatched in {sid}",
            )
    # Cookbook P2.3 — every sibling shares (action, target) by
    # construction, so we offer the auto-narrowing prompt ONCE using
    # the first member's action+target rather than N times.
    if members and result.get("n_approved", 0) > 0:
        _offer_relationship_capture(members[0]["action"], members[0]["target"])


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


def _paste_link(visible_text: str, command_to_paste: str) -> str:
    """Issue #18 — wrap `visible_text` in an OSC 8 hyperlink whose URI
    is `capdep://paste/<command>` on terminals that support OSC 8.
    Falls back to plain text on basic terminals. Used across audit /
    sessions / approvals / tools listings so operators can click
    any reference to paste the corresponding slash command into
    the input."""
    from urllib.parse import quote

    from capabledeputy.cli.terminal_caps import caps as _caps

    if not _caps().hyperlinks:
        return visible_text
    uri = f"capdep://paste/{quote(command_to_paste)}"
    return f"[link={uri}]{visible_text}[/link]"


def _handle_sessions() -> None:
    result = _call("session.list")
    sessions = result.get("sessions", [])
    if not sessions:
        console.print("[dim]no sessions[/dim]")
        return
    # Devbox enrichment — best-effort so older daemons w/o the handler
    # still get a plain (empty-column) view.
    devbox_summary: dict[str, dict[str, Any]] = {}
    try:
        devbox_summary = _call("devbox.summary_for_all").get("sessions", {})
    except Exception:
        devbox_summary = {}
    table = Table(title=f"Sessions ({len(sessions)})")
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Intent")
    table.add_column("Labels")
    table.add_column("Devbox", justify="right")
    for s in sessions:
        sid = s["id"]
        # Issue #18 — click the session id to paste /switch into input
        clickable_id = _paste_link(sid[:8], f"/switch {sid}")
        devbox_info = devbox_summary.get(sid, {})
        devbox_cell = _format_devbox_cell(devbox_info)
        table.add_row(
            clickable_id,
            s["status"],
            (s.get("intent") or "")[:40],
            ", ".join(s.get("label_set", [])) or "-",
            devbox_cell,
        )
    console.print(table)


def _format_devbox_cell(info: dict[str, Any]) -> str:
    """One short line summarizing a session's devbox state. Empty
    when the session has no devbox state at all. Three pieces:
    live count + workspace size + (when live) spec list.

    Examples:
      ""                          — no state, no row noise
      "[dim]0/1[/dim] 124 KiB"    — workspace dir exists, no live container
      "[green]1/1[/green] 38 MiB py-dev" — live container + workspace
    """
    if not info:
        return ""
    n_live = info.get("n_live", 0)
    n_dirs = info.get("n_workspace_dirs", 0)
    size = info.get("total_workspace_bytes", 0)
    specs = info.get("spec_ids") or []
    if n_live == 0 and n_dirs == 0 and size == 0:
        return ""
    size_text = _format_bytes(size) if size else "—"
    live_seg = f"[green]{n_live}/{n_dirs}[/green]" if n_live else f"[dim]0/{n_dirs}[/dim]"
    spec_seg = (" " + ", ".join(specs)) if specs else ""
    return f"{live_seg} {size_text}{spec_seg}"


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


def _send_message(
    session_id: str,
    message: str,
    max_iterations: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"session_id": session_id, "message": message}
    if max_iterations is not None:
        params["max_iterations"] = max_iterations
    return _call("session.send", params)


def _send_message_streaming(
    session_id: str,
    message: str,
    max_iterations: int | None = None,
    no_stream: bool = False,
    shared_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Issue #22 — send a message and render per-iteration progress
    via Rich Live while the daemon's session.send RPC runs.

    Mechanism: subscribe to the daemon's audit stream and consume
    events for *this session* as they arrive, rendering a spinner
    + activity line in a Rich Live region above the prompt. When
    session.send returns, the Live region exits cleanly and the
    final turn renders normally (via _render_turn).

    Falls back to the non-streaming path when:
    - `no_stream=True` is forced by the operator
    - terminal isn't a TTY (scripts / pipes)
    - the daemon doesn't support the audit subscribe stream

    This is the MVP version of #22: per-iteration progress (one event
    per LLM call + tool dispatch). True token-by-token streaming
    requires the LLM client + daemon RPC protocol to also stream;
    that's a separate follow-on.
    """
    if no_stream or not console.is_terminal:
        return _send_message(session_id, message, max_iterations)

    params: dict[str, Any] = {"session_id": session_id, "message": message}
    if max_iterations is not None:
        params["max_iterations"] = max_iterations

    # Run subscribe + RPC concurrently inside one anyio event loop.
    # The Live region runs in the main thread; events arrive via the
    # subscriber task; the RPC runs in a separate task and posts the
    # final result to a shared holder dict.
    result_holder: dict[str, Any] = {}

    async def _run() -> None:
        from rich.live import Live
        from rich.spinner import Spinner
        from rich.text import Text

        # Most-recent activity line; updated by the audit consumer.
        state = {"line": "thinking...", "iter": 0, "n_tools": 0}

        def _render() -> Any:
            from rich.console import Group

            spinner = Spinner("dots", text=Text(state["line"], style="dim cyan"))
            return Group(spinner)

        rpc_client = _client()

        async def _drain_audit(scope: Any) -> None:
            """Consume audit events for our session_id; update state."""
            try:
                async for evt in await rpc_client.subscribe(["audit"]):
                    if scope.cancel_called:
                        return
                    data = evt.get("data") or {}
                    if str(data.get("session_id", "")) != str(session_id):
                        continue
                    et = data.get("event_type", "")
                    payload = data.get("payload") or {}
                    if et == "llm.request_sent":
                        state["iter"] = payload.get("n_messages", state["iter"])
                        n_tools = payload.get("n_tools", state["n_tools"])
                        state["n_tools"] = n_tools
                        state["line"] = f"asking LLM ({state['n_tools']} tools available)..."
                        # Surface the freshly-computed context estimate
                        # into the toolbar's state dict so the bottom
                        # band can render `ctx 24k/200k 12%` in real
                        # time. Stale until the next turn fires — but
                        # the post-turn value is still informative.
                        ct = payload.get("context_tokens_estimate")
                        cw = payload.get("context_window")
                        if shared_state is not None and ct is not None:
                            shared_state["context_tokens"] = int(ct)
                            if cw is not None:
                                shared_state["context_window"] = int(cw)
                            # Timestamp lets the toolbar fade the
                            # segment to `(stale)` after 5 minutes —
                            # the value is from the LAST turn, not now,
                            # and stays accurate only as long as
                            # nothing else changed.
                            shared_state["context_tokens_at"] = datetime.now(UTC)
                    elif et == "llm.response_received":
                        clen = payload.get("content_length", 0)
                        n_tc = payload.get("n_tool_calls", 0)
                        state["line"] = f"received {clen}-char response · {n_tc} tool call(s)"
                        # Real provider usage — accumulate into both
                        # session and month-to-date totals so the
                        # toolbar's usage segment updates live as the
                        # turn progresses. Fakes / providers that
                        # don't report usage emit zeros, which leave
                        # the counters untouched.
                        pt = int(payload.get("prompt_tokens", 0) or 0)
                        ct_tok = int(payload.get("completion_tokens", 0) or 0)
                        if shared_state is not None and (pt or ct_tok):
                            shared_state["session_prompt_tokens"] = (
                                int(shared_state.get("session_prompt_tokens", 0)) + pt
                            )
                            shared_state["session_completion_tokens"] = (
                                int(shared_state.get("session_completion_tokens", 0)) + ct_tok
                            )
                            shared_state["mtd_prompt_tokens"] = (
                                int(shared_state.get("mtd_prompt_tokens", 0)) + pt
                            )
                            shared_state["mtd_completion_tokens"] = (
                                int(shared_state.get("mtd_completion_tokens", 0)) + ct_tok
                            )
                    elif et == "tool.dispatched":
                        tn = payload.get("tool_name", "?")
                        state["line"] = f"→ calling {tn}"
                    elif et == "tool.returned":
                        tn = payload.get("tool", "?")
                        out = payload.get("output", "")
                        out_len = len(str(out))
                        state["line"] = f"← {tn} returned ({out_len} bytes)"
                    elif et == "policy.decided":
                        d = (payload.get("decision") or "").upper()
                        if d == "DENY":
                            state["line"] = "[policy denied — finalizing]"
                        elif d == "REQUIRE_APPROVAL":
                            state["line"] = "[approval queued — finalizing]"
                    elif et == "llm.context_warning":
                        ratio = payload.get("ratio", 0.0)
                        state["line"] = f"⚠ context {int(ratio * 100)}% of window; summarizing soon"
            except Exception:
                return  # subscriber died; live keeps showing last line

        async def _send_rpc() -> None:
            try:
                result_holder["result"] = await rpc_client.call("session.send", params)
            except Exception as e:
                result_holder["error"] = e

        # Issue #23 — convert SIGINT (Ctrl-C) into a session.cancel
        # RPC so the operator can stop a runaway turn without killing
        # the REPL. First press cancels cleanly; second press
        # re-raises so the operator can still escape if cancel is
        # wedged (slow RPC, daemon hung, etc.).
        sigint_count = {"n": 0}

        async def _watch_sigint() -> None:
            import signal as _sig

            with anyio.open_signal_receiver(_sig.SIGINT) as signals:
                async for _ in signals:
                    sigint_count["n"] += 1
                    if sigint_count["n"] == 1:
                        state["line"] = "⊘ cancelling…"
                        # Best-effort. If cancel RPC fails, the
                        # second Ctrl-C still escapes.
                        with contextlib.suppress(Exception):
                            await rpc_client.call(
                                "session.cancel",
                                {"session_id": session_id},
                            )
                    else:
                        raise KeyboardInterrupt

        # Drive subscriber + RPC + signal watcher concurrently. anyio
        # task group cancels everything when the RPC completes.
        import anyio

        async with anyio.create_task_group() as tg:
            with Live(_render(), console=console, refresh_per_second=4, transient=True) as live:
                tg.start_soon(_drain_audit, tg.cancel_scope)
                tg.start_soon(_send_rpc)
                tg.start_soon(_watch_sigint)
                # Re-render until RPC settles
                while "result" not in result_holder and "error" not in result_holder:
                    live.update(_render())
                    await anyio.sleep(0.25)
                live.update(_render())
                # Cancel the audit subscriber + signal watcher now
                # that the RPC is done
                tg.cancel_scope.cancel()

    try:
        anyio.run(_run)
    except Exception:
        # Anything goes wrong with streaming → fall back to non-streaming
        # to preserve the operator's ability to actually complete the
        # turn. Audit-emitted events still made it to the log.
        result_holder.pop("error", None)
        return _send_message(session_id, message, max_iterations)

    if "error" in result_holder:
        raise result_holder["error"]
    return result_holder.get("result", {})


_HELP = """slash commands (user-only, never visible to the LLM):

  session: /sessions /session [id] /switch <id> /whoami
           /spawn <intent> [--bare] — clean child (inherits parent
                                       non-destructive caps unless --bare)
           /abort [id]              — abort session (current if no id)
           /grant <KIND> <pattern> [--one-shot --destructive --max-amount N]
           /status /labels /caps

  approval: /approvals  /approve <id>  /approve-group <gid>  /deny <id>  /submit
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

  copy:    /copy recovery       — copy latest recovery commands
           /copy approval <id>  — copy verbatim approval payload
           /copy last           — copy last agent response
           /copy trace <turn>   — copy turn's audit trace as JSON
           /copy <literal>      — copy arbitrary text

  daemon:  /server  /info  /daemon  — running daemon: version,
                                       uptime, tool count, sessions,
                                       custom kinds, drift detection

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
    # Issue #35 — kind may be a built-in (uppercased) or a custom
    # namespaced one like `slack:dm.send` (kept as typed). Detect
    # the namespace form via the `:` separator and DON'T uppercase
    # it — namespaced custom kinds are lowercase by convention.
    raw_kind = parts[0]
    kind = raw_kind if ":" in raw_kind else raw_kind.upper()
    pattern = parts[1]
    rest = parts[2:]

    # Improvement roadmap #8 — surface warnings for patterns that
    # don't match the kind's target shape (e.g. `/grant SEND_EMAIL
    # spouse` quietly succeeds today but never fires). We don't
    # block: operators may have legitimate unusual conventions, and
    # a hard reject would force them to bypass the warning system.
    # `--no-warn` suppresses (script-mode for tests / automation).
    if "--no-warn" not in rest:
        from capabledeputy.policy.grant_validation import (
            validate_grant_pattern,
        )

        warnings = validate_grant_pattern(kind, pattern)
        if warnings:
            for w in warnings:
                err_console.print(f"[yellow]warning:[/yellow] {w}")
            confirm = (
                Prompt.ask(
                    "proceed with this grant anyway? [y/N]",
                    default="N",
                )
                .strip()
                .lower()
            )
            if confirm not in ("y", "yes"):
                console.print("[dim]grant cancelled[/dim]")
                return
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


def _handle_first_use(arg: str, session_id: str) -> None:
    """`/first-use on | off | status` — flip the per-session first-
    action-of-kind prompt flag (cookbook §4 #6).

    ON: the FIRST dispatch of any promptable kind (SEND_EMAIL,
    QUEUE_PURCHASE, MODIFY_FS/DELETE_FS, MODIFY_CAL/DELETE_CAL,
    EXECUTE_SANDBOX/DEVBOX) in this session escalates to
    REQUIRE_APPROVAL even when a standing capability exists. Once
    approved, subsequent dispatches of the same kind sail through.
    Reads are excluded (would create approval fatigue from every
    new mailbox label / file path).

    OFF (default): every dispatch passes through whatever the rule
    composition produced. Sessions spawned from a `cautious`
    purpose auto-enable this; the operator can flip it later."""
    mode = arg.strip().lower()
    if mode in ("", "status"):
        info = _call("session.get", {"session_id": session_id})
        enabled = bool(info.get("first_use_prompt_enabled", False))
        if enabled:
            console.print(
                "  first-use prompts: [yellow]on[/yellow]  "
                "[dim](first dispatch of each promptable kind "
                "requires approval)[/dim]",
            )
        else:
            console.print(
                "  first-use prompts: [dim]off[/dim]  "
                "[dim](dispatches gated only by standing rules)[/dim]",
            )
        return
    if mode in ("on", "true", "enable", "yes"):
        enabled = True
    elif mode in ("off", "false", "disable", "no"):
        enabled = False
    else:
        err_console.print(
            f"[red]usage:[/red] /first-use on | off | status  (got {arg!r})",
        )
        return
    try:
        result = _call(
            "session.set_first_use_prompts",
            {"session_id": session_id, "enabled": enabled},
        )
    except Exception as e:
        err_console.print(f"[red]set_first_use_prompts failed:[/red] {e}")
        return
    state = "[yellow]on[/yellow]" if result.get("first_use_prompt_enabled") else "[dim]off[/dim]"
    console.print(f"[green]✓[/green] first-use prompts now {state}")


def _handle_enforce(arg: str, session_id: str) -> None:
    """`/enforce shadow | strict | status` — flip the session's
    enforcement mode (cookbook Pattern ⑥).

    SHADOW: every non-ALLOW decision becomes ALLOW + audit; rule
    outcomes are observed but don't disrupt the agent. Use this
    for K turns of new-rule validation before flipping to STRICT.

    STRICT (default): every decision fires as authored — DENY
    blocks, REQUIRE_APPROVAL routes to approval, AUTO proceeds.

    `status` shows the current mode without changing it."""
    mode = arg.strip().lower()
    if mode in ("", "status"):
        info = _call("session.get", {"session_id": session_id})
        current = info.get("enforcement_mode", "strict")
        if current == "shadow":
            console.print(
                "  enforcement: [yellow]shadow[/yellow]  "
                "[dim](non-ALLOW outcomes silenced + audited)[/dim]",
            )
            console.print(
                "  [dim]Run [bold]/enforce strict[/bold] to flip to production enforcement.[/dim]",
            )
        else:
            console.print(
                "  enforcement: [green]strict[/green]  [dim](outcomes fire as authored)[/dim]",
            )
            console.print(
                "  [dim]Run [bold]/enforce shadow[/bold] to silence "
                "non-ALLOW outcomes and observe via the audit log.[/dim]",
            )
        return
    if mode not in ("shadow", "strict"):
        err_console.print(
            f"[red]usage:[/red] /enforce shadow | strict | status  (got {arg!r})",
        )
        return
    try:
        result = _call(
            "session.set_enforcement",
            {"session_id": session_id, "mode": mode},
        )
    except Exception as e:
        err_console.print(f"[red]set_enforcement failed:[/red] {e}")
        return
    new_mode = result.get("enforcement_mode", mode)
    style = "yellow" if new_mode == "shadow" else "green"
    console.print(
        f"[green]✓[/green] enforcement now [{style}]{new_mode}[/{style}]",
    )
    if new_mode == "shadow":
        console.print(
            "  [dim]non-ALLOW outcomes will be silenced; review via "
            "[bold]/audit[/bold] before flipping back to strict.[/dim]",
        )


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
    label_str = f" labels={','.join(labels_required)}" if labels_required else ""
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
            # Issue #18 — click the tool name to filter to it
            clickable_name = _paste_link(name, f"/tools {name}")
            console.print(f"  [bold]{clickable_name}[/bold]{effect_str}")


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


def _handle_server_info() -> None:
    """/server — daemon version + uptime + tool/session/kind counts.

    This is the operator-facing 'what's actually running' view.
    Useful when debugging stale-daemon issues (#10) or just
    confirming the daemon is current with source. Renders as a
    compact transcript-style block, not a heavy table — matches
    the chat conversation aesthetic established at 3db6828."""
    try:
        info = _call("daemon.info")
    except Exception as e:
        err_console.print(f"[red]daemon.info failed:[/red] {e}")
        return

    # Header
    version = info.get("version", "?")
    git_rev = (info.get("git_rev") or "")[:7]
    git_dirty = info.get("git_dirty", "")
    manifest = (info.get("manifest_hash") or "")[:8]
    pid = info.get("pid", "?")
    uptime_s = info.get("uptime_seconds", 0)
    uptime_str = _format_uptime(uptime_s)
    python_v = info.get("python_version", "?")

    git_label = f"git {git_rev}" if git_rev else "no-git"
    if git_dirty == "dirty":
        git_label += " [yellow](dirty)[/yellow]"

    console.print()
    console.print("[bold cyan]capdep daemon[/bold cyan]  [dim]/server[/dim]")
    console.print(f"  [dim]version[/dim]   capdep {version}  {git_label}  manifest {manifest}")
    console.print(f"  [dim]runtime[/dim]   PID {pid}  uptime {uptime_str}  python {python_v}")
    rss_mb = info.get("rss_mb", 0)
    if rss_mb:
        console.print(f"  [dim]memory[/dim]    RSS {rss_mb:,} MB")

    # Upstream MCP servers — per-server status (registered ok vs failed)
    upstream_servers = info.get("upstream_servers", [])
    if upstream_servers:
        console.print()
        n_ok = sum(1 for s in upstream_servers if s.get("state") == "registered")
        n_failed = sum(1 for s in upstream_servers if s.get("state") == "failed")
        header_bits = [f"{len(upstream_servers)} upstream server(s)"]
        if n_failed:
            header_bits.append(f"[red]{n_failed} failed[/red]")
        if n_ok:
            header_bits.append(f"[green]{n_ok} ok[/green]")
        console.print(f"  [dim]servers[/dim]   {' · '.join(header_bits)}")
        for srv in upstream_servers:
            name = srv.get("name", "?")
            state = srv.get("state", "?")
            n_tools = srv.get("registered_tool_count", 0)
            n_rej = srv.get("rejected_tool_count", 0)
            if state == "registered":
                line = f"            [green]✓[/green] [bold]{name}[/bold]  {n_tools} tool(s)"
                if n_rej:
                    line += f"  [yellow]({n_rej} rejected)[/yellow]"
                console.print(line)
                # Show rejected tool names for strict-mode classifier failures
                rej_names = srv.get("rejected_tool_names", [])
                if rej_names:
                    for rn in rej_names[:5]:
                        console.print(
                            f"              [dim]·[/dim] rejected: [yellow]{rn}[/yellow] [dim](unclassifiable; add to tool_mappings/tool_overrides)[/dim]"
                        )
                    if len(rej_names) > 5:
                        console.print(
                            f"              [dim]· ... +{len(rej_names) - 5} more rejections[/dim]"
                        )
            else:  # failed
                err = srv.get("error", "")
                console.print(f"            [red]✗[/red] [bold]{name}[/bold]  [red]FAILED[/red]")
                if err:
                    # Indent + word-wrap-friendly truncation
                    err_short = err if len(err) <= 100 else err[:100] + "…"
                    console.print(f"              [dim]error:[/dim] [red]{err_short}[/red]")
                cmd = srv.get("command", [])
                if cmd:
                    console.print(
                        f"              [dim]command:[/dim] {' '.join(cmd[:3])}{'...' if len(cmd) > 3 else ''}"
                    )

    # Tools
    tool_count = info.get("tool_count", 0)
    by_kind = info.get("tools_by_kind", {})
    console.print()
    console.print(f"  [dim]tools[/dim]     {tool_count} registered")
    if by_kind:
        # Show top kinds compactly
        sorted_kinds = sorted(by_kind.items(), key=lambda x: -x[1])
        kind_summary = ", ".join(f"{n} {k}" for k, n in sorted_kinds[:6])
        if len(sorted_kinds) > 6:
            kind_summary += f", +{len(sorted_kinds) - 6} more kinds"
        console.print(f"            [dim]{kind_summary}[/dim]")

    # Sessions
    n_sessions = info.get("session_count", 0)
    n_active = info.get("session_count_active", 0)
    console.print(f"  [dim]sessions[/dim]  {n_sessions} total, {n_active} active")

    # Custom kinds (Issue #35)
    n_custom = info.get("custom_kind_count", 0)
    if n_custom:
        console.print(f"  [dim]plugins[/dim]   {n_custom} custom kind(s) from servers.d/")
        for k in info.get("custom_kinds", [])[:8]:
            flag = "[yellow]destructive[/yellow]" if k.get("destructive") else "read-only"
            desc = k.get("description", "")
            line = f"            [bold]{k['name']}[/bold] ({flag})"
            if desc:
                line += f"  [dim]— {desc}[/dim]"
            console.print(line)
    else:
        console.print("  [dim]plugins[/dim]   none (no custom kinds loaded from servers.d/)")

    # Audit log
    audit_size = info.get("audit_size_bytes", 0)
    audit_path = info.get("audit_path", "")
    if audit_path:
        console.print(
            f"  [dim]audit[/dim]     {_format_bytes(audit_size)} at {audit_path}",
        )

    # Drift check
    try:
        code_version = _call("daemon.code_version")
        local_manifest = _local_manifest_hash()
        running_manifest = code_version.get("manifest_hash", "")
        if local_manifest and running_manifest and local_manifest != running_manifest:
            console.print()
            console.print(
                f"  [yellow]⚠ drift:[/yellow] running manifest "
                f"[red]{running_manifest[:8]}[/red] differs from source "
                f"[green]{local_manifest[:8]}[/green]",
            )
            console.print(
                "  [dim]restart with [bold]capdep daemon stop[/bold] && "
                "[bold]capdep chat[/bold] to pick up local changes.[/dim]",
            )
    except Exception:
        pass


def _format_uptime(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h {m}m"
    d, rem = divmod(seconds, 86400)
    h = rem // 3600
    return f"{d}d {h}h"


def _format_bytes(n: int) -> str:
    """Human-readable byte count (KB/MB/GB)."""
    size: float = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _local_manifest_hash() -> str:
    """Recompute the manifest hash against the current source tree.
    Mirrors the daemon's _capture_code_version logic so drift
    detection compares apples-to-apples."""
    import hashlib

    try:
        from pathlib import Path as _Path

        from capabledeputy import __file__ as cap_init

        src_root = _Path(cap_init).resolve().parent
        h = hashlib.sha256()
        for py in sorted(src_root.rglob("*.py")):
            try:
                stat = py.stat()
                h.update(f"{py.relative_to(src_root)}:{stat.st_mtime_ns}:{stat.st_size}".encode())
            except OSError:
                continue
        return h.hexdigest()[:16]
    except Exception:
        return ""


def _handle_copy(
    arg: str,
    session_id: str,
    last_result: dict[str, Any] | None,
) -> None:
    """/copy <what> — push capdep artifacts to the system clipboard
    via OSC 52 on supporting terminals (Issue #20).

    Subcommands:
      /copy recovery        — most recent recovery-step sequence
      /copy approval <id>   — verbatim payload of an approval
      /copy last            — most recent agent response
      /copy trace <turn>    — turn's full audit trace as JSON
      /copy <text...>       — copy literal text (escape hatch)

    Fallback when OSC 52 isn't supported: writes to a per-session
    file under ~/.capdep/clipboard/ and tells the operator the path.
    """
    import base64
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    from capabledeputy.cli.terminal_caps import caps as _caps

    parts = arg.split(maxsplit=1)
    if not parts:
        err_console.print(
            "[red]usage:[/red] /copy recovery | /copy approval <id> | "
            "/copy last | /copy trace <turn> | /copy <literal text>",
        )
        return
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    content: str = ""
    label: str = ""

    if sub == "recovery":
        # Most recent recovery sequence — pull from last_result's
        # tool_outcomes (denied or preview-deny rows carry the steps).
        if not last_result:
            err_console.print("[dim]no turn yet — nothing to copy[/dim]")
            return
        steps: list[str] = []
        for o in last_result.get("tool_outcomes", []) or []:
            outcome_steps = o.get("recovery_steps") or []
            if not outcome_steps and isinstance(o.get("output"), dict):
                outcome_steps = o["output"].get("recovery_steps") or []
            for s in outcome_steps:
                cmd = s.get("command", "")
                args = s.get("args", [])
                steps.append(f"{cmd} {' '.join(args)}".strip())
        if not steps:
            err_console.print("[dim]no recovery steps on the last turn[/dim]")
            return
        content = "\n".join(steps) + "\n"
        label = f"recovery sequence ({len(steps)} step(s))"

    elif sub == "approval":
        if not rest.strip():
            err_console.print("[red]usage:[/red] /copy approval <id>")
            return
        try:
            show = _call("approval.show", {"id": int(rest.strip())})
        except Exception as e:
            err_console.print(f"[red]approval.show failed:[/red] {e}")
            return
        content = show.get("payload", "") or ""
        label = f"approval #{rest.strip()} verbatim payload"

    elif sub == "last":
        if not last_result:
            err_console.print("[dim]no turn yet — nothing to copy[/dim]")
            return
        content = last_result.get("content", "") or ""
        label = "last agent response"

    elif sub == "trace":
        if not rest.strip().isdigit():
            err_console.print("[red]usage:[/red] /copy trace <turn-number>")
            return
        try:
            result = _call(
                "audit.list",
                {"session_id": session_id, "limit": 1000},
            )
        except Exception as e:
            err_console.print(f"[red]audit.list failed:[/red] {e}")
            return
        turn = int(rest.strip())
        events = [e for e in result.get("events", []) if e.get("turn_id") == turn]
        if not events:
            err_console.print(f"[dim]no events for turn {turn}[/dim]")
            return
        content = _json.dumps(events, indent=2, default=str)
        label = f"trace for turn {turn} ({len(events)} event(s))"

    else:
        # Literal-text escape hatch
        content = arg
        label = f"literal text ({len(content)} chars)"

    if not content:
        err_console.print("[dim]nothing to copy[/dim]")
        return

    # OSC 52: ESC ] 52 ; c ; <base64> BEL  — c = clipboard selection.
    # Length cap of ~100 KB on most terminals; if we're over, fall
    # back to file-on-disk path.
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    use_osc52 = _caps().clipboard and len(encoded) < 100_000

    if use_osc52:
        # Emit the escape directly to stdout so Rich doesn't escape it.
        _sys.stdout.write(f"\033]52;c;{encoded}\007")
        _sys.stdout.flush()
        console.print(f"[green]✓ copied to clipboard:[/green] {label}")
        return

    # Fallback: write to ~/.capdep/clipboard/<timestamp>.txt
    import datetime as _dt

    fallback_dir = _Path.home() / ".capdep" / "clipboard"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    fallback_path = fallback_dir / f"{ts}-{sub}.txt"
    fallback_path.write_text(content, encoding="utf-8")
    reason = (
        "terminal doesn't support OSC 52"
        if not _caps().clipboard
        else "content > 100KB (OSC 52 cap)"
    )
    console.print(
        f"[yellow]wrote to file[/yellow] (clipboard unavailable: {reason}): {fallback_path}",
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
                '[--floor F] [--justification "..."]',
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
            f"[green]✓ override grant[/green] #{str(grant_id)[:8]} [dim](state={state})[/dim]",
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
        '  /override request <KIND> <target> [--floor F] [--justification "..."]\n'
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


# How long until the toolbar's context segment is considered stale.
# Chosen to roughly match the Anthropic prompt cache TTL (5 min) — if
# nothing has happened in 5 minutes, the most-recent context estimate
# is unlikely to still reflect what the agent would send NOW.
CONTEXT_STALENESS_THRESHOLD = timedelta(minutes=5)


def _toolbar_context_segment(
    tokens: int | None,
    window: int | None,
    measured_at: datetime | None = None,
    *,
    now: datetime | None = None,
) -> str:
    """Render `│ ctx 24k/200k 12%` for the bottom toolbar. Color
    escalates from gray (cool, <60%) → yellow (warn, 60-80%) →
    red (cliff, >80%) so the operator gets a peripheral-vision cue
    long before the agent hits the wall.

    When `measured_at` is older than CONTEXT_STALENESS_THRESHOLD,
    the segment dims to gray regardless of the percentage and
    appends `(stale)` — the post-turn estimate doesn't reflect the
    current state, so it shouldn't shout warning colors at the
    operator. `now` is injectable so tests don't have to mock time.

    Returns empty when no turn has fired yet (tokens/window not
    yet populated)."""
    if tokens is None or window is None or window <= 0:
        return ""
    ratio = tokens / window
    pct = int(ratio * 100)

    # Staleness check first — overrides the color escalation so a
    # stale 90% reading doesn't look like an active emergency.
    stale = False
    if measured_at is not None:
        ts_now = now if now is not None else datetime.now(UTC)
        if ts_now - measured_at >= CONTEXT_STALENESS_THRESHOLD:
            stale = True

    if stale:
        color = "ansigray"
    elif ratio >= 0.80:
        color = "ansired"
    elif ratio >= 0.60:
        color = "ansiyellow"
    else:
        color = "ansigray"

    stale_suffix = " (stale)" if stale else ""
    return (
        f" │ <{color}>ctx {_short_tokens(tokens)}/{_short_tokens(window)} "
        f"{pct}%{stale_suffix}</{color}>"
    )


def _short_tokens(n: int) -> str:
    """Compact token counts for the bottom toolbar: 12,345→12k,
    4,500→4.5k, 0→0. Shared by the context and usage segments so
    both columns line up."""
    if n >= 10_000:
        return f"{n // 1000}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _toolbar_usage_segment(
    session_prompt: int | None,
    session_completion: int | None,
    mtd_prompt: int | None,
    mtd_completion: int | None,
) -> str:
    """Render `│ tok 1.2k↑/340↓ · mtd 87k↑/12k↓` for the bottom
    toolbar. Sums real provider usage from `llm.response_received`
    events — the streaming consumer accumulates `session_*` per
    REPL session, and `_read_mtd_usage` seeds `mtd_*` at startup
    from `audit.jsonl`.

    Returns empty when no usage has been recorded yet, so the
    toolbar collapses cleanly on a brand-new session before the
    first turn fires."""
    sp = session_prompt or 0
    sc = session_completion or 0
    mp = mtd_prompt or 0
    mc = mtd_completion or 0
    if sp == 0 and sc == 0 and mp == 0 and mc == 0:
        return ""
    return (
        f" │ <ansigray>tok {_short_tokens(sp)}↑/{_short_tokens(sc)}↓ "
        f"· mtd {_short_tokens(mp)}↑/{_short_tokens(mc)}↓</ansigray>"
    )


def _read_mtd_usage(
    audit_path: Path | str | None,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Scan `audit.jsonl` once and sum prompt/completion tokens from
    every `llm.response_received` event whose timestamp falls in the
    current calendar month.

    Cheap enough at typical log sizes (~MB) to do synchronously at
    REPL startup; if the file is missing or unparseable, returns
    (0, 0) so the toolbar just shows the session-only counts. `now`
    is injectable so tests don't have to mock the clock."""
    import json

    if not audit_path:
        return (0, 0)
    path = Path(audit_path)
    if not path.exists():
        return (0, 0)
    ts_now = now if now is not None else datetime.now(UTC)
    month_start = ts_now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    prompt_total = 0
    completion_total = 0
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return (0, 0)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event_type") != "llm.response_received":
            continue
        ts_str = event.get("timestamp")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < month_start:
            continue
        payload = event.get("payload") or {}
        prompt_total += int(payload.get("prompt_tokens", 0) or 0)
        completion_total += int(payload.get("completion_tokens", 0) or 0)
    return (prompt_total, completion_total)


def _make_bottom_toolbar(
    cache: CompletionCache,
    focus: dict[str, str],
    state: dict[str, Any] | None = None,
):
    """Two-line status band (#24).

    Line 1: identity + IFC state (session, compartment, labels,
    cap counts, pending approvals).
    Line 2: contextual bindings hint — F-key recovery shown only
    when recovery_steps are available, otherwise the basic shortcuts.

    Reads CompletionCache (polled every ~1s for the completer) plus
    a mutable `state` dict shared with the REPL loop. State carries
    transient info that's not in the daemon: last error glyph,
    available recovery steps, lifecycle markers. Re-renders on
    every prompt-toolkit refresh tick (1.0s configured).
    """

    state = state if state is not None else {}

    def render() -> HTML:
        sid = focus["id"]
        sess = next((s for s in cache.sessions if s["id"] == sid), None)
        short = sid[:8]
        if sess is None:
            return HTML(
                f" session <b>{short}</b> · <ansicyan>(syncing…)</ansicyan> \n"
                f" <ansigray>Tab complete · Alt-Enter newline · ? help · /quit to exit</ansigray>"
            )
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

        # Context-window usage from the most-recent turn (updated by
        # the streaming audit consumer in _send_message_streaming).
        # Renders as `ctx 24k/200k 12%` with color escalating from
        # gray → yellow → red as the ratio approaches the cliff;
        # fades to `(stale)` after 5 minutes idle.
        ctx_seg = _toolbar_context_segment(
            state.get("context_tokens"),
            state.get("context_window"),
            state.get("context_tokens_at"),
        )

        # Session + month-to-date token spend. `session_*` is
        # accumulated by the streaming consumer in
        # _send_message_streaming as `llm.response_received` events
        # land; `mtd_*` is seeded at REPL startup by
        # _read_mtd_usage and bumped by the same consumer so the
        # number stays current within a session.
        usage_seg = _toolbar_usage_segment(
            state.get("session_prompt_tokens"),
            state.get("session_completion_tokens"),
            state.get("mtd_prompt_tokens"),
            state.get("mtd_completion_tokens"),
        )

        # Line 1 — identity + IFC state
        line1 = (
            f" session <b>{short}</b> "
            f"│ compartment {word_tag}: {comp} "
            f"│ caps {ncaps}{ttl_seg}{pending_seg}"
            f"{ctx_seg}{usage_seg} "
        )

        # Line 2 — bindings hint, contextual
        # When recovery steps are available, show F-key bindings.
        # When approval is pending, show 'a' to review.
        # Otherwise, show the basic shortcuts.
        hints = []
        recovery_steps = state.get("recovery_steps") or []
        if recovery_steps:
            f_keys = []
            for i, step in enumerate(recovery_steps[:3], start=1):
                cmd = (
                    step.get("command", "")
                    if isinstance(step, dict)
                    else getattr(step, "command", "")
                )
                f_keys.append(f"<ansicyan><b>F{i}</b></ansicyan> {cmd}")
            hints.append(" · ".join(f_keys))
        if npending:
            hints.append("<ansiyellow><b>a</b></ansiyellow> review approval")
        # Always-visible shortcut reminders
        if not hints:
            hints.append(
                "<ansigray>Tab complete · Alt-Enter newline · ? help · /quit to exit</ansigray>"
            )
        else:
            # When contextual hints are present, append a minimal reminder
            hints.append("<ansigray>Tab · ? help</ansigray>")

        line2 = " " + " · ".join(hints) + " "

        return HTML(line1 + "\n" + line2)

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

        # Lightweight rule + indented body — no heavy Panel. Reads
        # as part of the conversation transcript rather than a modal.
        console.print()
        console.print(f"  [yellow]approval #{aid} · verbatim payload[/yellow]")
        console.print("  [dim]" + "─" * 60 + "[/dim]")
        for line_text in (show["payload"] or "").splitlines():
            console.print(f"  {line_text}")
        console.print("  [dim]" + "─" * 60 + "[/dim]")
        console.print("  [dim]this is exactly what will happen if you approve[/dim]")
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
                subprocess.run(
                    [editor, tmp_path],
                    check=False,
                )
                with open(tmp_path, encoding="utf-8") as f:
                    edited_payload = f.read()
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

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
                            justification + f" (edited from approval #{aid})"
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


def _repl_loop(session_id: str, no_stream: bool = False) -> None:
    # Mutable focus: /switch rebinds these to retarget session.send.
    focus = {"id": session_id, "label": _short_label(session_id)}
    last_result: dict[str, Any] | None = None

    # Mutable REPL state shared with the bottom toolbar (#24) + key
    # bindings (#26 F-key recovery). Updated by _run_repl after each
    # turn: recovery_steps from the most recent denied outcome,
    # lifecycle markers, etc.
    state: dict[str, Any] = {
        "recovery_steps": [],
        "lifecycle": "idle",
        # Session-local token spend, accumulated by the streaming
        # consumer as `llm.response_received` audit events arrive.
        # Both default to 0 so the toolbar segment formats cleanly
        # on a brand-new session before the first turn lands.
        "session_prompt_tokens": 0,
        "session_completion_tokens": 0,
        # Month-to-date spend, seeded from audit.jsonl at startup.
        # `daemon.info` is the source of truth for the audit path;
        # if it's unavailable (offline daemon, older build) the
        # toolbar just shows session-only counts.
        "mtd_prompt_tokens": 0,
        "mtd_completion_tokens": 0,
    }
    try:
        _info_for_mtd = _call("daemon.info")
        _audit_path = _info_for_mtd.get("audit_path", "")
        _mp, _mc = _read_mtd_usage(_audit_path)
        state["mtd_prompt_tokens"] = _mp
        state["mtd_completion_tokens"] = _mc
    except Exception:
        # MTD is decorative — don't block REPL startup if the
        # daemon or audit log is unreachable.
        pass

    cache = CompletionCache(daemon_call=_call)
    cache.start()

    # Issue #16 — multi-line input. Default to single-line; Alt-Enter
    # (Esc-then-Enter on most terminals) inserts a literal newline so
    # multi-paragraph messages and pasted code blocks work. Enter still
    # submits. Bracketed-paste mode (prompt-toolkit enables it by
    # default when supported) means pasted content with internal
    # newlines is treated as text, not as multiple commands — also a
    # win for safety.
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("escape", "enter")  # Alt-Enter
    def _newline(event) -> None:  # pyright: ignore[reportUnusedFunction]
        event.current_buffer.insert_text("\n")

    # Issue #26 — F1/F2/F3 execute recovery steps from the most
    # recent denied outcome. Bound conditionally: only fire when
    # state["recovery_steps"] is non-empty. The Condition is
    # re-evaluated on every keypress so steps clear naturally when
    # the next turn lands.
    def _recovery_available() -> bool:
        return bool(state.get("recovery_steps"))

    recovery_condition = Condition(_recovery_available)

    def _run_recovery_step(event, idx: int) -> None:
        steps = state.get("recovery_steps") or []
        if idx >= len(steps):
            return
        step = steps[idx]
        cmd = step.get("command", "") if isinstance(step, dict) else getattr(step, "command", "")
        args = step.get("args") or [] if isinstance(step, dict) else list(getattr(step, "args", []))
        command_line = f"{cmd} {' '.join(args)}".strip()
        # Populate the buffer with the command and submit immediately.
        # Clearing state["recovery_steps"] happens implicitly when the
        # next turn renders new outcomes.
        event.current_buffer.text = command_line
        event.current_buffer.validate_and_handle()

    @kb.add("f1", filter=recovery_condition)
    def _recover_1(event) -> None:  # pyright: ignore[reportUnusedFunction]
        _run_recovery_step(event, 0)

    @kb.add("f2", filter=recovery_condition)
    def _recover_2(event) -> None:  # pyright: ignore[reportUnusedFunction]
        _run_recovery_step(event, 1)

    @kb.add("f3", filter=recovery_condition)
    def _recover_3(event) -> None:  # pyright: ignore[reportUnusedFunction]
        _run_recovery_step(event, 2)

    pt_session = PromptSession(
        history=FileHistory(str(_history_path())),
        completer=CapDepCompleter(cache),
        complete_while_typing=False,
        bottom_toolbar=_make_bottom_toolbar(cache, focus, state),
        refresh_interval=1.0,
        multiline=False,  # Enter submits; Alt-Enter inserts newline
        key_bindings=kb,
    )

    console.print()
    # Operator-stats banner — fetch once at startup so the user
    # sees what they're actually talking to. Falls back gracefully
    # if daemon.info isn't available (e.g. running an older daemon).
    daemon_blurb = ""
    try:
        info = _call("daemon.info")
        version = info.get("version", "?")
        git_rev = (info.get("git_rev") or "")[:7]
        dirty = " (dirty)" if info.get("git_dirty") == "dirty" else ""
        n_tools = info.get("tool_count", 0)
        n_custom = info.get("custom_kind_count", 0)
        daemon_blurb = (
            f"capdep {version}"
            + (f" · git {git_rev}{dirty}" if git_rev else "")
            + f" · {n_tools} tool{'s' if n_tools != 1 else ''}"
            + (f" · {n_custom} plugin kind{'s' if n_custom != 1 else ''}" if n_custom else "")
        )
    except Exception:
        daemon_blurb = "capdep daemon (info unavailable)"
    console.print(f"[dim]{daemon_blurb}[/dim]")
    console.print(
        f"[bold]capdep chat[/bold]  [dim]session {focus['id'][:8]}[/dim]",
    )
    console.print(
        "[dim]Type a message to chat, or use a slash command. "
        "Try [bold]/help[/bold] for commands, [bold]/tools[/bold] to see what "
        "the agent can do, [bold]/server[/bold] for daemon details.[/dim]",
    )
    console.print(
        "[dim]Shortcuts: TAB completion · ↑/↓ history · Alt-Enter newline · "
        "F1-3 run recovery commands · /quit to exit[/dim]",
    )
    try:
        _run_repl(pt_session, focus, last_result, state, no_stream=no_stream)
    finally:
        cache.stop()


def _run_repl(
    pt_session: PromptSession,
    focus: dict[str, str],
    last_result: dict[str, Any] | None,
    state: dict[str, Any] | None = None,
    no_stream: bool = False,
) -> None:
    while True:
        # Issue #25 — lifecycle state glyph on the prompt.
        # Reads from the shared state dict; transitions are driven by
        # _run_repl after each turn. Future #21 streaming work adds
        # 'sending' and 'streaming' transitions during a turn.
        lifecycle = (state or {}).get("lifecycle", "idle")
        glyph_map = {
            "idle": "",
            "sending": "<ansiyellow>⏳</ansiyellow>",
            "streaming": "<ansicyan>📡</ansicyan>",
            "failed": "<ansired>✗</ansired>",
        }
        glyph = glyph_map.get(lifecycle, "")
        glyph_str = f"{glyph} " if glyph else ""
        try:
            line = pt_session.prompt(
                HTML(
                    f"<ansicyan><b>{focus['label']}</b></ansicyan>{glyph_str}> ",
                ),
            ).rstrip()
        except KeyboardInterrupt:
            # Issue #23 — Ctrl-C at idle prompt clears the line buffer
            # (prompt-toolkit already did that before raising) and
            # stays in the REPL. Modern AI-chat UX: Ctrl-C cancels,
            # never exits. Ctrl-D / `/quit` / `exit` / `bye` exit.
            continue
        except EOFError:
            console.print("\n[dim]bye[/dim]")
            return
        # Issue #25 — clear transient state glyphs once the operator
        # has seen them (we're about to dispatch a new turn).
        if state is not None and state.get("lifecycle") in ("failed",):
            state["lifecycle"] = "idle"
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
            if cmd == "enforce":
                _handle_enforce(arg, focus["id"])
                continue
            if cmd in ("first-use", "first_use"):
                _handle_first_use(arg, focus["id"])
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
            if cmd in ("approve-group", "approve_group"):
                _handle_approve_group(arg)
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
            if cmd == "copy":
                _handle_copy(arg, focus["id"], last_result)
                continue
            if cmd in ("server", "info", "daemon"):
                _handle_server_info()
                continue
            err_console.print(f"[red]unknown command:[/red] /{cmd}")
            continue

        # Echo the user's input into scrollback so the conversation
        # reads as a transcript (not just agent output following
        # invisible prompt-toolkit input).
        _render_user_message(line)
        try:
            last_result = _send_message_streaming(
                focus["id"],
                line,
                max_iterations=_SESSION_MAX_ITERS.get("value"),
                no_stream=no_stream,
                shared_state=state,
            )
        except Exception as e:
            err_console.print(f"[red]rpc error:[/red] {e}")
            if state is not None:
                state["lifecycle"] = "failed"
                state["recovery_steps"] = []
            continue
        _render_turn(last_result)
        # Update the shared state so the bottom toolbar (#24) and the
        # F-key recovery bindings (#26) reflect the latest turn's
        # recovery_steps. Take the first denied outcome's steps; if
        # multiple outcomes denied, the first one's recovery is the
        # one the operator hits first when scanning.
        if state is not None:
            outcomes = last_result.get("tool_outcomes", []) or []
            steps: list = []
            for o in outcomes:
                outcome_steps = o.get("recovery_steps") or []
                if not outcome_steps and isinstance(o.get("output"), dict):
                    outcome_steps = o["output"].get("recovery_steps") or []
                if outcome_steps:
                    steps = list(outcome_steps)
                    break
            state["recovery_steps"] = steps
            state["lifecycle"] = "idle"
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


# Cookbook P2.4 — persona shortcuts. Map a friendly --persona flag
# to a configs/purposes.yaml purpose_id. Custom purpose ids may be
# passed directly via --persona <id> when not one of the built-ins.
PERSONA_TO_PURPOSE: dict[str, str] = {
    "daily-life": "daily-life-management",
    "finance": "personal-finance",
    "health": "phi-management",
    "dev": "software-dev",
}


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
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help=(
                "Surface selection (#15). `auto` (default) detects the "
                "terminal — rich Textual surface on Ghostty / kitty / "
                "iTerm2 / WezTerm / Alacritty, line mode elsewhere. "
                "`line` forces the line-oriented prompt-toolkit REPL. "
                "`rich` forces the Textual surface (errors if the "
                "terminal doesn't support it). Defaults to `auto`."
            ),
        ),
    ] = "auto",
    max_iters: Annotated[
        int,
        typer.Option(
            "--max-iters",
            help=(
                "Max LLM iterations per turn. The agent loop will call "
                "tools and feed results back until it hits this cap or "
                "returns a final answer. Default 50 — multi-step tasks "
                "(summarize-inbox, audit-codebase) often need 20+. Bump "
                "for complex workflows; lower for cost control."
            ),
        ),
    ] = 50,
    no_stream: Annotated[
        bool,
        typer.Option(
            "--no-stream",
            help=(
                "Issue #22: disable the inline progress region during a "
                "turn. By default chat subscribes to the daemon audit "
                "stream and renders a spinner + most-recent activity "
                "line (calling X, received Y) while the turn runs. "
                "Pass --no-stream to fall back to the legacy "
                "block-on-rpc-then-print behavior (or when piping "
                "output)."
            ),
        ),
    ] = False,
    persona: Annotated[
        str | None,
        typer.Option(
            "--persona",
            help=(
                "Cookbook persona for an auto-created session. Maps to "
                "a Purpose in configs/purposes.yaml. Built-in personas: "
                "`daily-life` → daily-life-management, `finance` → "
                "personal-finance, `health` → phi-management, `dev` → "
                "software-dev. Loads the purpose's default capabilities "
                "as standing grants at spawn (no per-call /grant). "
                "Ignored when resuming an existing session."
            ),
        ),
    ] = None,
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

    # Persona → purpose_handle mapping (see PERSONA_TO_PURPOSE at
    # module scope). Custom personas can be passed as the literal
    # purpose_id when the alias isn't one of the built-ins.
    purpose_handle: str | None = None
    if persona is not None:
        purpose_handle = PERSONA_TO_PURPOSE.get(persona, persona)

    effective_id: str | None = None if new else session_id
    auto_created = False
    if effective_id is None:
        params: dict[str, Any] = {"intent": intent or "chat"}
        if purpose_handle is not None:
            params["purpose_handle"] = purpose_handle
        try:
            s = _call("session.new", params)
        except Exception as e:
            # Surface QuarantinedLLMUnavailableError + admissibility
            # errors as readable CLI output rather than tracebacks.
            err_console.print(
                f"[red]session.new failed:[/red] {e}",
            )
            raise typer.Exit(code=1) from None
        effective_id = str(s["id"])
        auto_created = True
        if purpose_handle is not None:
            console.print(
                f"[green]new session:[/green] {effective_id}  "
                f"intent={intent or 'chat'}  "
                f"[dim]purpose={purpose_handle}[/dim]",
            )
        else:
            console.print(
                f"[green]new session:[/green] {effective_id}  intent={intent or 'chat'}",
            )

    # Pre-grant useful read-only caps on auto-created sessions so the
    # agent doesn't fail at "I have no tools available" — the user
    # shouldn't have to chant /grant for safe reads.
    # Skip when a persona was used: the purpose's default_capabilities
    # already covered the standing-grant story, and double-granting
    # would clutter the cap list.
    if auto_created and not no_default_caps and purpose_handle is None:
        _grant_default_read_caps(effective_id)

    # Store max_iters for the session so every send_message call
    # uses it. Per-session override via env var if needed.
    import os as _os_for_iters

    env_override = _os_for_iters.environ.get("CAPDEP_MAX_ITERS")
    effective_max_iters = int(env_override) if env_override else max_iters
    _SESSION_MAX_ITERS["value"] = effective_max_iters

    # Issue #15 Phase B — surface dispatch.
    # `--mode auto` (default) checks terminal capability detection
    # from terminal_caps.caps() and picks rich for known-good
    # families, line otherwise. Explicit `--mode line|rich` overrides.
    _dispatch_surface(effective_id, mode, no_stream=no_stream)


def _dispatch_surface(session_id: str, mode: str, no_stream: bool = False) -> None:
    """Issue #15 — pick the chat surface based on `mode` + terminal
    capabilities. Line mode is the current `_repl_loop`; rich mode
    is the Textual surface in cli/rich_surface.py."""
    from capabledeputy.cli.terminal_caps import caps as _caps

    mode = (mode or "auto").lower()
    if mode not in ("auto", "line", "rich"):
        err_console.print(
            f"[red]invalid --mode value:[/red] {mode!r}; expected auto / line / rich",
        )
        raise typer.Exit(code=2)

    if mode == "line":
        _repl_loop(session_id, no_stream=no_stream)
        return

    c = _caps()
    if mode == "rich":
        # Forced rich — fail loudly if the terminal won't support it.
        if not c.is_tty:
            err_console.print(
                "[red]--mode rich requires a TTY; falling through to "
                "line mode would surprise scripts.[/red]",
            )
            raise typer.Exit(code=2)
        _run_rich_surface(session_id)
        return

    # mode == "auto"
    rich_families = ("ghostty", "kitty", "iterm2", "wezterm", "alacritty")
    if c.family in rich_families and c.is_tty:
        _run_rich_surface(session_id)
    else:
        _repl_loop(session_id, no_stream=no_stream)


def _run_rich_surface(session_id: str) -> None:
    """Phase B scaffold for the rich Textual surface (#15).

    Currently a thin wrapper around the existing `tui/console.py`
    CapDepConsole — the half-finished convergence attempt — promoted
    to first-class entry point. Phase C will add the tabbed viewer
    side-pane (#17). Phase D will add OSC 8 hyperlinks + sixel
    graphics (#18, #19). Until feature parity with line mode is
    reached, slash commands like /grant / /override are best run
    from line mode; the rich surface focuses on conversation + live
    label-state monitoring + verbatim approval review (the things
    the existing CapDepConsole already implements).
    """
    from capabledeputy.tui.console import CapDepConsole

    console.print(
        "[dim]launching rich surface (Phase B scaffold; line mode "
        "still has the full slash-command surface)[/dim]",
    )
    CapDepConsole(session_id).run()


def _grant_default_read_caps(session_id: str) -> None:
    """Grant the safe, read-only capability set every personal-assistant
    session needs: filesystem reads, calendar reads, web fetches, and
    common modify-style operations.

    The chokepoint still enforces label propagation, Brewer-Nash, and
    every other rule on top — these caps only mean "this kind of
    action is generally permitted for this session", not "let
    everything through".
    """
    # Issue #6 — Scope READ_FS away from system files. Previously this
    # was `READ_FS *` which let the agent read /etc/passwd, ~/.ssh/*,
    # ~/.aws/*, etc. The scoped set covers normal work dirs; the agent
    # can still `/grant READ_FS <path>` for anything outside.
    #
    # ~ expansion is intentionally NOT done here — the daemon's pattern
    # matcher does shell-style globbing and `~` is fine as a literal.
    # If your home isn't /home/<you>, edit your auto-grant via /grant.
    import os as _os
    from uuid import uuid4

    home = _os.path.expanduser("~")
    default_caps = (
        # Filesystem reads — scoped to operator's work dirs (Issue #6).
        ("READ_FS", f"{home}/Documents/*"),
        ("READ_FS", f"{home}/Projects/*"),
        ("READ_FS", f"{home}/Downloads/*"),
        ("READ_FS", f"{home}/Desktop/*"),
        ("READ_FS", "/tmp/*"),
        # Personal-assistant reads (Issue #33 partial fix) — granular
        # kinds so the operator's email / drive access doesn't depend
        # on a sketchy "READ_FS *" or "READ_FS gmail:*" hack. These are
        # read-only and inherently safe to default-grant; sensitive
        # operations like SEND_EMAIL / DELETE / share stay behind
        # explicit /grant per the destructive-kinds rule.
        ("GMAIL_READ", "*"),
        ("IMAP_READ", "*"),
        ("DRIVE_READ", "*"),
        ("CALENDAR_READ", "*"),
        ("WEB_FETCH", "*"),
        # Sandbox + scratch workspace creation
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
