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
                    placeholder="message the agent…  (/quit to exit)",
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
        self.query_one("#log", RichLog).write(f"[bold]you[/bold] {text}")
        self._send(text)

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
