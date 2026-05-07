"""capdep tui: live monitoring and approval interface (DESIGN.md §10.3).

Connects to a running daemon via the existing JSON-RPC client. Polls
every poll_interval seconds for sessions, approvals, and recent
audit events. Approvals can be approved or denied directly from the
TUI; the verbatim payload is rendered byte-for-byte (§8.2 requirement)
so the user always sees exactly what would be sent.

This is the v0.1 minimum-viable Textual app. Full session-graph view,
trace pane, and pattern-rule editor are v0.2 follow-ups.
"""

from __future__ import annotations

from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Static

from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path


class ApprovalDetailScreen(ModalScreen[str]):
    """Modal that renders an approval request's verbatim payload and
    captures the user's decision (approve / deny / cancel)."""

    BINDINGS = [  # noqa: RUF012
        Binding("a", "approve", "Approve", show=True),
        Binding("d", "deny", "Deny", show=True),
        Binding("escape", "dismiss('cancel')", "Cancel"),
    ]

    DEFAULT_CSS = """
    ApprovalDetailScreen {
        align: center middle;
    }
    #detail-box {
        width: 90%;
        max-width: 100;
        height: 80%;
        border: thick $primary;
        padding: 1 2;
        background: $surface;
    }
    #payload {
        background: $boost;
        padding: 1;
        height: 1fr;
        overflow: auto;
        border: solid $accent;
    }
    """

    def __init__(self, approval: dict[str, Any]) -> None:
        super().__init__()
        self._approval = approval

    def compose(self) -> ComposeResult:
        a = self._approval
        with Vertical(id="detail-box"):
            yield Static(
                f"[bold]Approval #{a['id']}[/bold]  action={a['action']}  status={a['status']}",
            )
            yield Static(f"target:  {a['target']}")
            yield Static(f"from session:  {a['from_session']}")
            yield Static(
                "labels in:  " + (", ".join(a["labels_in"]) or "(none)"),
            )
            yield Static(f"justification:  {a['justification']}")
            yield Static("[bold]payload (verbatim):[/bold]")
            yield Static(a["payload"], id="payload")
            yield Static(
                "[dim]a=approve  d=deny  esc=cancel[/dim]",
            )

    def action_approve(self) -> None:
        self.dismiss("approve")

    def action_deny(self) -> None:
        self.dismiss("deny")


class CapDepTUI(App[None]):
    BINDINGS = [  # noqa: RUF012
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("enter", "open_approval", "Open approval", show=True),
    ]

    CSS = """
    Screen { layout: vertical; }
    #panes { height: 1fr; }
    #left, #right { width: 50%; }
    DataTable { height: 1fr; }
    .pane-title { background: $primary 30%; padding: 0 1; }
    """

    def __init__(self, poll_interval: float = 1.5) -> None:
        super().__init__()
        self._client = DaemonClient(default_socket_path())
        self._poll_interval = poll_interval
        self._approvals: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="panes"):
            with Vertical(id="left"):
                yield Static("Sessions", classes="pane-title")
                yield DataTable(id="sessions")
                yield Static("Events (recent)", classes="pane-title")
                yield DataTable(id="events")
            with Vertical(id="right"):
                yield Static("Approvals (pending)", classes="pane-title")
                yield DataTable(id="approvals")
        yield Footer()

    def on_mount(self) -> None:
        sessions = self.query_one("#sessions", DataTable)
        sessions.add_columns("id", "status", "intent", "labels")
        sessions.cursor_type = "row"

        events = self.query_one("#events", DataTable)
        events.add_columns("ts", "type", "session")

        approvals = self.query_one("#approvals", DataTable)
        approvals.add_columns("id", "action", "target", "labels")
        approvals.cursor_type = "row"

        self.set_interval(self._poll_interval, self._refresh)
        self.refresh_now()

    def action_refresh(self) -> None:
        self.refresh_now()

    def refresh_now(self) -> None:
        self._refresh()

    @work(exclusive=True)
    async def _refresh(self) -> None:
        try:
            sessions_resp = await self._client.call("session.list", {})
            approvals_resp = await self._client.call(
                "approval.list",
                {"status": "pending"},
            )
            audit_resp = await self._client.call("audit.tail", {"limit": 30})
        except DaemonNotRunningError:
            self.notify(
                "daemon not running — start it with `capdep daemon start`",
                severity="warning",
            )
            return

        sessions_table = self.query_one("#sessions", DataTable)
        sessions_table.clear()
        for s in sessions_resp["sessions"]:
            sessions_table.add_row(
                s["id"][:8],
                s["status"],
                s["intent"] or "",
                ", ".join(s["label_set"]),
            )

        events_table = self.query_one("#events", DataTable)
        events_table.clear()
        for ev in reversed(audit_resp["events"][-30:]):
            events_table.add_row(
                ev["timestamp"][11:19],
                ev["event_type"],
                (ev.get("session_id") or "")[:8],
            )

        approvals_table = self.query_one("#approvals", DataTable)
        approvals_table.clear()
        self._approvals = approvals_resp["approvals"]
        for a in self._approvals:
            approvals_table.add_row(
                str(a["id"]),
                a["action"],
                a["target"],
                ", ".join(a["labels_in"]),
            )

    def action_open_approval(self) -> None:
        approvals_table = self.query_one("#approvals", DataTable)
        if not approvals_table.is_valid_row_index(approvals_table.cursor_row):
            return
        if approvals_table.cursor_row >= len(self._approvals):
            return
        chosen = self._approvals[approvals_table.cursor_row]
        self.push_screen(
            ApprovalDetailScreen(chosen),
            self._handle_approval_decision_for(chosen["id"]),
        )

    def _handle_approval_decision_for(self, approval_id: int):
        async def handler(decision: str | None) -> None:
            if decision in (None, "cancel"):
                return
            try:
                if decision == "approve":
                    await self._client.call(
                        "approval.approve",
                        {"id": approval_id},
                    )
                    self.notify(f"approval #{approval_id} approved")
                elif decision == "deny":
                    await self._client.call("approval.deny", {"id": approval_id})
                    self.notify(f"approval #{approval_id} denied", severity="warning")
            except Exception as e:
                self.notify(f"error: {e}", severity="error")
            self.refresh_now()

        return handler


def run() -> None:
    CapDepTUI().run()
