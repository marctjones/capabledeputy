"""capdep tui: live monitoring and approval interface (DESIGN.md §10.3).

Connects to a running daemon via the existing JSON-RPC client. Polls
every poll_interval seconds for sessions, approvals, and recent
audit events. Approvals can be approved or denied directly from the
TUI; the verbatim payload is rendered byte-for-byte (§8.2 requirement)
so the user always sees exactly what would be sent.

v0.2 upgrades on the v0.1 minimum-viable three-pane app:

  - Five logical panes in the layout: Sessions, Approvals (left
    column), Conversation, Trace (right column), Events (bottom).
  - Selecting a session in the Sessions pane populates the
    Conversation pane (its turn history) and the Trace pane (recent
    audit events scoped to that session).
  - Selecting an approval still opens the verbatim-payload modal.
  - 'g' toggles a session-graph (indented tree by parent) view in
    the Sessions pane.

Real-time event push (instead of 1.5s polling) is deferred to v0.3 —
needs a streaming JSON-RPC variant on the daemon side.
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
    ApprovalDetailScreen { align: center middle; }
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
            yield Static("[dim]a=approve  d=deny  esc=cancel[/dim]")

    def action_approve(self) -> None:
        self.dismiss("approve")

    def action_deny(self) -> None:
        self.dismiss("deny")


class CapDepTUI(App[None]):
    BINDINGS = [  # noqa: RUF012
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("g", "toggle_graph", "Graph view", show=True),
        Binding("enter", "open_approval", "Open approval", show=True),
    ]

    CSS = """
    Screen { layout: vertical; }
    #panes { height: 1fr; }
    #left, #right { width: 50%; }
    .pane-title {
        background: $primary 30%;
        padding: 0 1;
        text-style: bold;
    }
    DataTable { height: 1fr; }
    #conversation, #trace {
        height: 1fr;
        overflow: auto;
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(self, poll_interval: float = 1.5) -> None:
        super().__init__()
        self._client = DaemonClient(default_socket_path())
        self._poll_interval = poll_interval
        self._approvals: list[dict[str, Any]] = []
        self._sessions: list[dict[str, Any]] = []
        self._selected_session_id: str | None = None
        self._graph_view: bool = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="panes"):
            with Vertical(id="left"):
                yield Static("Sessions", classes="pane-title")
                yield DataTable(id="sessions")
                yield Static("Approvals (pending)", classes="pane-title")
                yield DataTable(id="approvals")
            with Vertical(id="right"):
                yield Static("Conversation", classes="pane-title")
                yield Static("(select a session)", id="conversation")
                yield Static("Trace", classes="pane-title")
                yield Static("(select a session)", id="trace")
        yield Static("Events (live ticker)", classes="pane-title")
        yield DataTable(id="events")
        yield Footer()

    def on_mount(self) -> None:
        sessions = self.query_one("#sessions", DataTable)
        sessions.add_columns("id", "status", "intent", "labels")
        sessions.cursor_type = "row"

        events = self.query_one("#events", DataTable)
        events.add_columns("ts", "type", "session", "decision/labels")

        approvals = self.query_one("#approvals", DataTable)
        approvals.add_columns("id", "action", "target", "labels")
        approvals.cursor_type = "row"

        self.set_interval(self._poll_interval, self._refresh)
        self.refresh_now()

    def action_refresh(self) -> None:
        self.refresh_now()

    def action_toggle_graph(self) -> None:
        self._graph_view = not self._graph_view
        self.notify(
            "graph view: on" if self._graph_view else "graph view: off",
        )
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
            audit_resp = await self._client.call("audit.tail", {"limit": 50})
        except DaemonNotRunningError:
            self.notify(
                "daemon not running — start it with `capdep daemon start`",
                severity="warning",
            )
            return

        self._sessions = sessions_resp["sessions"]
        self._render_sessions()

        events_table = self.query_one("#events", DataTable)
        events_table.clear()
        for ev in reversed(audit_resp["events"][-40:]):
            decision_or_labels = ""
            payload = ev.get("payload") or {}
            if ev["event_type"] == "policy.decided":
                decision_or_labels = payload.get("decision", "")
            elif ev["event_type"] == "label.propagated":
                decision_or_labels = "+" + ",".join(payload.get("labels_added", []))
            elif ev["event_type"] == "approval.requested":
                decision_or_labels = f"approval #{payload.get('approval_id', '')}"
            events_table.add_row(
                ev["timestamp"][11:19],
                ev["event_type"],
                (ev.get("session_id") or "")[:8],
                decision_or_labels,
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

        if self._selected_session_id:
            await self._update_session_detail(self._selected_session_id, audit_resp["events"])

    def _render_sessions(self) -> None:
        sessions_table = self.query_one("#sessions", DataTable)
        sessions_table.clear()

        if self._graph_view:
            by_parent: dict[str | None, list[dict[str, Any]]] = {}
            for s in self._sessions:
                by_parent.setdefault(s.get("parent"), []).append(s)

            def emit(parent_id: str | None, depth: int) -> None:
                for s in by_parent.get(parent_id, []):
                    indent = "  " * depth + ("└─ " if depth > 0 else "")
                    sessions_table.add_row(
                        indent + s["id"][:8],
                        s["status"],
                        s["intent"] or "",
                        ", ".join(s["label_set"]),
                    )
                    emit(s["id"], depth + 1)

            emit(None, 0)
        else:
            for s in self._sessions:
                sessions_table.add_row(
                    s["id"][:8],
                    s["status"],
                    s["intent"] or "",
                    ", ".join(s["label_set"]),
                )

    async def _update_session_detail(
        self,
        session_id: str,
        recent_events: list[dict[str, Any]],
    ) -> None:
        try:
            full = await self._client.call("session.get", {"session_id": session_id})
        except Exception:
            return

        history = full.get("history") or []
        if not history:
            convo = "(no turns yet)"
        else:
            lines: list[str] = []
            for turn in history[-15:]:
                role = turn["role"]
                content = (turn["content"] or "")[:600]
                if role == "user":
                    lines.append("[bold cyan]user[/bold cyan]")
                elif role == "agent":
                    lines.append("[bold green]agent[/bold green]")
                else:
                    lines.append(f"[bold]{role}[/bold]")
                for content_line in content.split("\n"):
                    lines.append(f"  {content_line}")
                lines.append("")
            convo = "\n".join(lines)

        self.query_one("#conversation", Static).update(convo)

        scoped = [e for e in recent_events if (e.get("session_id") or "") == session_id]
        if not scoped:
            trace_text = "(no recent events for this session)"
        else:
            lines = []
            for ev in scoped[-30:]:
                ts = ev["timestamp"][11:19]
                t = ev["event_type"]
                payload = ev.get("payload") or {}
                summary = ""
                if t == "policy.decided":
                    summary = (
                        f"{payload.get('decision', '')} "
                        + (f"rule={payload.get('rule')} " if payload.get("rule") else "")
                        + f"tool={payload.get('tool', '')}"
                    )
                elif t == "label.propagated":
                    summary = "+" + ",".join(payload.get("labels_added", []))
                elif t == "tool.dispatched":
                    summary = payload.get("tool", "")
                elif t == "approval.requested":
                    summary = (
                        f"approval #{payload.get('approval_id', '')} "
                        f"target={payload.get('target', '')}"
                    )
                lines.append(f"[dim]{ts}[/dim] [bold]{t}[/bold] {summary}")
            trace_text = "\n".join(lines)

        self.query_one("#trace", Static).update(trace_text)

    def on_data_table_row_highlighted(self, event: Any) -> None:
        # Pyright can't narrow Textual events here; accept Any.
        if event.data_table.id != "sessions":
            return
        if not self._sessions:
            return
        row = event.cursor_row
        if row is None or row >= len(self._sessions):
            return
        if self._graph_view:
            self._selected_session_id = None
            return
        self._selected_session_id = self._sessions[row]["id"]
        self.refresh_now()

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
