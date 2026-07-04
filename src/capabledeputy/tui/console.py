# pyright: reportUnusedCoroutine=false
# Textual's handler API accepts async functions that the framework
# fires-and-forgets via run_worker / call_after; pyright reads these
# as bare-coroutine-never-awaited. These are intentional per the
# framework contract, so we silence the rule at file scope rather
# than tagging every call site. This file is also slated for removal
# once `capdep chat --mode rich` reaches feature parity (#15 Phase C).
"""capdep console: a single window that drives the agent, monitors
the security model live, and grants approvals.

Unlike `capdep tui` (read-only spectator) this one has an input box:
type to the agent, watch the conversation + per-tool policy trace in
the main pane, watch the compartment / capability constraints update
live in the sidebar, and review+approve in the verbatim modal the
moment an approval is queued — all without a second terminal.

The shell is deliberately thin. Every formatting / selection decision
lives in the unit-tested `console_model`; the approval modal is the
same `ApprovalDetailScreen` the spectator TUI uses. Enforcement is
entirely server-side — this only calls `session.send` /
`approval.{approve,deny}` and renders what comes back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.tui.app import ApprovalDetailScreen
from capabledeputy.tui.console_model import (
    format_history_turn,
    format_turn,
    pending_approvals,
    status_lines,
)


class CapDepConsole(App[None]):
    BINDINGS = [  # noqa: RUF012
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+a", "open_approval", "Approvals", show=True),
    ]

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #main { width: 70%; }
    #side { width: 30%; }
    #log { height: 1fr; border: solid $accent; padding: 0 1; }
    #status {
        height: 1fr; border: solid $primary; padding: 0 1;
        background: $surface; overflow: auto;
    }
    #prompt { dock: bottom; }
    .pane-title { background: $primary 30%; padding: 0 1; text-style: bold; }
    """

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self._session_id = session_id
        self._client = DaemonClient(default_socket_path())
        self._pending: list[int] = []
        self._history_loaded = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="main"):
                yield Static("Conversation + policy trace", classes="pane-title")
                yield RichLog(id="log", markup=True, wrap=True)
                yield Input(
                    placeholder="message the agent…  (/help for commands, /quit to exit)",
                    id="prompt",
                )
            with Vertical(id="side"):
                yield Static("Live security state", classes="pane-title")
                yield Static("(loading…)", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#log", RichLog).write(
            f"[dim]session {self._session_id[:8]} — type to drive the "
            f"agent; approvals pop up for verbatim review[/dim]",
        )
        self.query_one("#prompt", Input).focus()
        self._refresh_status()
        self._event_stream()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#prompt", Input).value = ""
        if not text:
            return
        if text in ("/quit", "/exit"):
            self.exit()
            return
        if text.startswith("/"):
            self.query_one("#log", RichLog).write(f"[bold]command[/bold] {escape(text)}")
            self._handle_slash(text)
            return
        self.query_one("#log", RichLog).write(f"[bold]you[/bold] {text}")
        self._send(text)

    @work(exclusive=True)
    async def _handle_slash(self, line: str) -> None:
        log = self.query_one("#log", RichLog)
        cmd, _, arg = line[1:].partition(" ")
        cmd = cmd.lower()
        try:
            if cmd == "help":
                self._write_help(log)
            elif cmd == "whoami":
                log.write(escape(self._session_id))
            elif cmd == "switch":
                await self._cmd_switch(arg, log)
            elif cmd == "session":
                await self._cmd_session(arg, log)
            elif cmd == "spawn":
                await self._cmd_spawn(arg, log)
            elif cmd == "grant":
                await self._cmd_grant(arg, log)
            elif cmd == "status":
                await self._cmd_status(log)
            elif cmd == "labels":
                await self._cmd_status(log, only="labels")
            elif cmd == "caps":
                await self._cmd_status(log, only="caps")
            elif cmd == "schemas":
                await self._cmd_schemas(log)
            elif cmd == "extract":
                await self._cmd_extract(arg, log)
            elif cmd == "approvals":
                await self._cmd_approvals(log)
            elif cmd == "approve":
                await self._cmd_approve(arg, log)
            elif cmd == "respond":
                await self._cmd_respond(arg, log)
            elif cmd == "deny":
                await self._cmd_deny(arg, log)
            else:
                log.write(f"[red]unknown command:[/red] /{escape(cmd)}")
        except DaemonNotRunningError:
            log.write("[red]daemon not running[/red]")
        except Exception as e:
            log.write(f"[red]command error:[/red] {escape(str(e))}")
        self._refresh_status()

    def _write_help(self, log: RichLog) -> None:
        log.write(
            "[bold]commands[/bold] /help /quit /whoami /switch <id> /session [id] "
            "/spawn <intent> [--bare] /grant <KIND> <pattern> "
            "[--one-shot --destructive --max-amount N --ttl S] /status /labels /caps "
            "/schemas /extract <msg> <schema> /approvals /approve <id> "
            "/respond <id> <json> /deny <id>",
        )

    async def _cmd_switch(self, arg: str, log: RichLog) -> None:
        target = arg.strip()
        if not target:
            log.write("[red]usage:[/red] /switch <session-id>")
            return
        self._session_id = target
        self._history_loaded = False
        log.write(f"[green]→[/green] now talking to [cyan]{escape(target[:8])}[/cyan]")
        await self._refresh_status_now()

    async def _cmd_session(self, arg: str, log: RichLog) -> None:
        target = arg.strip() or self._session_id
        result = await self._client.call("session.get", {"session_id": target})
        log.write(
            f"[bold]session[/bold] {escape(str(result.get('id', target))[:8])} "
            f"[dim]status={escape(str(result.get('status', '?')))}[/dim]",
        )
        for line in status_lines(result):
            log.write(escape(line))

    async def _cmd_spawn(self, arg: str, log: RichLog) -> None:
        parts = arg.split()
        bare = "--bare" in parts
        intent = " ".join(p for p in parts if p != "--bare").strip()
        if not intent:
            intent = "user-spawned clean session"
        new = await self._client.call(
            "session.new",
            {"intent": intent, "parent": self._session_id},
        )
        new_id = str(new["id"])
        await self._client.call(
            "session.add_labels",
            {"session_id": new_id, "labels": ["trusted.user_direct"]},
        )
        inherited = 0
        if not bare:
            parent = await self._client.call("session.get", {"session_id": self._session_id})
            for cap in parent.get("capability_set", []):
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
                await self._client.call(
                    "session.grant_capability",
                    {"session_id": new_id, "capability": cap_copy},
                )
                inherited += 1
        self._session_id = new_id
        self._history_loaded = False
        log.write(
            f"[green]✓ spawned[/green] [cyan]{escape(new_id[:8])}[/cyan] "
            f"[dim]trusted.user_direct, inherited={inherited}[/dim]",
        )

    async def _cmd_grant(self, arg: str, log: RichLog) -> None:
        parts = arg.split()
        if len(parts) < 2:
            log.write("[red]usage:[/red] /grant <KIND> <pattern> [flags]")
            return
        raw_kind = parts[0]
        kind = raw_kind if ":" in raw_kind else raw_kind.upper()
        pattern = parts[1]
        rest = parts[2:]
        max_amount = None
        if "--max-amount" in rest:
            idx = rest.index("--max-amount")
            max_amount = int(rest[idx + 1])
        expires_at = None
        if "--ttl" in rest:
            idx = rest.index("--ttl")
            ttl = int(rest[idx + 1])
            expires_at = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat()
        cap = {
            "kind": kind,
            "pattern": pattern,
            "expiry": "one_shot" if "--one-shot" in rest else "session",
            "origin": "user_approved",
            "audit_id": str(uuid4()),
            "max_amount": max_amount,
            "allows_destructive": "--destructive" in rest,
            "revoked_by": [],
        }
        if expires_at is not None:
            cap["expires_at"] = expires_at
        await self._client.call(
            "session.grant_capability",
            {"session_id": self._session_id, "capability": cap},
        )
        log.write(f"[green]✓ granted[/green] {escape(kind)} {escape(pattern)}")

    async def _cmd_status(self, log: RichLog, *, only: str | None = None) -> None:
        result = await self._client.call("session.get", {"session_id": self._session_id})
        if only == "labels":
            log.write(f"[bold]labels[/bold] {escape(str(result.get('label_set', [])))}")
            return
        if only == "caps":
            caps = result.get("capability_set", [])
            if not caps:
                log.write("[dim]no capabilities[/dim]")
                return
            for cap in caps:
                log.write(
                    f"[bold]{escape(str(cap.get('kind', '?')))}[/bold] "
                    f"{escape(str(cap.get('pattern', '*')))}",
                )
            return
        for line in status_lines(result):
            log.write(escape(line))

    async def _cmd_schemas(self, log: RichLog) -> None:
        result = await self._client.call("extract.schemas", {})
        schemas = result.get("schemas", [])
        if not schemas:
            log.write("[dim]no schemas available[/dim]")
            return
        log.write("[bold]schemas[/bold] " + escape(", ".join(map(str, schemas))))

    async def _cmd_extract(self, arg: str, log: RichLog) -> None:
        parts = arg.split()
        if len(parts) < 2:
            log.write("[red]usage:[/red] /extract <message_id> <schema>")
            return
        result = await self._client.call(
            "extract.inbox_message",
            {"message_id": parts[0], "schema": parts[1]},
        )
        if "error" in result:
            log.write(f"[red]extract failed:[/red] {escape(str(result['error']))}")
            return
        log.write(
            "[green]declassified[/green] "
            + escape(json.dumps(result.get("data", {}), sort_keys=True)),
        )

    async def _cmd_approvals(self, log: RichLog) -> None:
        result = await self._client.call("approval.list", {"status": "pending"})
        approvals = result.get("approvals", [])
        if not approvals:
            log.write("[dim]no pending approvals[/dim]")
            return
        self._pending = [int(a["id"]) for a in approvals if "id" in a]
        for approval in approvals:
            log.write(
                f"[yellow]#{approval.get('id')}[/yellow] "
                f"{escape(str(approval.get('action', '?')))} → "
                f"{escape(str(approval.get('target', '?')))}",
            )

    async def _cmd_approve(self, arg: str, log: RichLog) -> None:
        if not arg.strip():
            log.write("[red]usage:[/red] /approve <id>")
            return
        approval_id = int(arg.strip())
        self._pending = [approval_id]
        log.write(f"[yellow]opening approval #{approval_id}[/yellow]")
        self._open_approval(approval_id)

    async def _cmd_respond(self, arg: str, log: RichLog) -> None:
        raw_id, _, raw_json = arg.strip().partition(" ")
        if not raw_id or not raw_json:
            log.write("[red]usage:[/red] /respond <approval-id> <json-object>")
            return
        approval_id = int(raw_id)
        response_value = json.loads(raw_json)
        if not isinstance(response_value, dict):
            log.write("[red]response must be a JSON object[/red]")
            return
        await self._client.call(
            "approval.respond_elicitation",
            {
                "id": approval_id,
                "response_value": response_value,
                "decided_by": "console",
            },
        )
        log.write(f"[green]✓ responded[/green] elicitation #{approval_id}")

    async def _cmd_deny(self, arg: str, log: RichLog) -> None:
        if not arg.strip():
            log.write("[red]usage:[/red] /deny <id>")
            return
        approval_id = int(arg.strip())
        await self._client.call(
            "approval.deny",
            {"id": approval_id, "reason": "denied via console"},
        )
        log.write(f"[yellow]denied[/yellow] approval #{approval_id}")

    async def _refresh_status_now(self) -> None:
        full = await self._client.call("session.get", {"session_id": self._session_id})
        self.query_one("#status", Static).update("\n".join(status_lines(full)))

    @work(exclusive=True)
    async def _send(self, message: str) -> None:
        log = self.query_one("#log", RichLog)
        try:
            result = await self._client.call(
                "session.send",
                {"session_id": self._session_id, "message": message},
            )
        except DaemonNotRunningError:
            log.write("[red]daemon not running[/red]")
            return
        except Exception as e:  # surface RPC errors in-pane
            log.write(f"[red]rpc error:[/red] {e}")
            return
        for line in format_turn(result):
            log.write(line)
        self._refresh_status()
        ids = pending_approvals(result)
        if ids:
            self._pending = ids
            log.write(
                f"[yellow]→ approval{'s' if len(ids) > 1 else ''} "
                f"{', '.join(f'#{i}' for i in ids)} queued — "
                f"opening verbatim review[/yellow]",
            )
            self._open_approval(ids[0])

    @work(exclusive=False)
    async def _refresh_status(self) -> None:
        try:
            full = await self._client.call(
                "session.get",
                {"session_id": self._session_id},
            )
        except Exception:
            return
        self.query_one("#status", Static).update(
            "\n".join(status_lines(full)),
        )
        if not self._history_loaded:
            self._history_loaded = True
            history = full.get("history") or []
            if history:
                log = self.query_one("#log", RichLog)
                for turn in history:
                    for line in format_history_turn(turn):
                        log.write(line)

    @work(exclusive=False)
    async def _event_stream(self) -> None:
        try:
            async for ev in await self._client.subscribe(["audit"]):
                data = ev.get("data") or {}
                if (data.get("session_id") or "") == self._session_id:
                    self._refresh_status()
        except Exception:
            return

    def action_open_approval(self) -> None:
        if self._pending:
            self._open_approval(self._pending[0])
        else:
            self.notify("no pending approvals")

    @work(exclusive=False)
    async def _open_approval(self, approval_id: int) -> None:
        try:
            full = await self._client.call(
                "approval.show",
                {"id": approval_id},
            )
        except Exception as e:
            self.notify(f"could not load approval #{approval_id}: {e}", severity="error")
            return
        self.push_screen(
            ApprovalDetailScreen(full),
            self._decide_for(approval_id),
        )

    def _decide_for(self, approval_id: int):
        async def handler(decision: str | None) -> None:
            log = self.query_one("#log", RichLog)
            if decision in (None, "cancel"):
                log.write(f"[dim]approval #{approval_id} left pending[/dim]")
                return
            try:
                if decision == "approve":
                    res = await self._client.call(
                        "approval.approve",
                        {"id": approval_id},
                    )
                    log.write(f"[green]✓ approved #{approval_id}[/green]")
                    if res.get("executed_in_session"):
                        d = res.get("dispatch", {})
                        log.write(
                            f"  [dim]dispatched in "
                            f"{res['executed_in_session'][:8]} "
                            f"({d.get('decision', '?')})[/dim]",
                        )
                else:
                    await self._client.call(
                        "approval.deny",
                        {"id": approval_id},
                    )
                    log.write(f"[yellow]denied #{approval_id}[/yellow]")
            except Exception as e:
                log.write(f"[red]error:[/red] {e}")
            self._pending = [i for i in self._pending if i != approval_id]
            self._refresh_status()

        return handler


def run(session_id: str) -> None:
    CapDepConsole(session_id).run()
