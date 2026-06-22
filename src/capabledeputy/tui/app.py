# pyright: reportUnusedCoroutine=false
# Textual handler-API: fire-and-forget coroutines triggered via the
# framework's call_after / run_worker plumbing are intentional. Slated
# for removal once `capdep chat --mode rich` reaches feature parity
# (#15 Phase C deprecates this surface).
"""capdep tui: live monitoring and approval interface (DESIGN.md §10.3).

Connects to a running daemon via the existing JSON-RPC client. Subscribes
to the daemon's audit stream and pushes events into the Events pane in
real time; approvals + sessions refresh on a 5-second backstop poll.
Approvals can be approved or denied directly from the TUI; the verbatim
payload is rendered byte-for-byte (§8.2 requirement) so the user always
sees exactly what would be sent.

Layout (DESIGN.md §10.3 target reached):

  - Five logical panes: Sessions, Approvals (left column),
    Conversation, Trace (right column), Events (bottom ticker).
  - Selecting a session in the Sessions pane populates the
    Conversation pane (its turn history) and the Trace pane (recent
    audit events scoped to that session).
  - Selecting an approval opens the verbatim-payload modal.
  - 'g' toggles a session-graph (indented tree by parent) view in
    the Sessions pane.

The 5s backstop poll catches state we don't push (the in-memory
session graph snapshot, the approvals queue), while the audit
subscription handles the high-frequency event ticker. Push and poll
together cover the full state model without doubling load.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Static

from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.presentation import (
    DENY_RECOVERY,
    capability_line,
    compartment_summary,
    render_labels,
)


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
        Binding("o", "list_overrides", "Overrides", show=True),
        Binding("p", "pause_session", "Pause", show=True),
        Binding("u", "resume_session", "Resume", show=True),
        Binding("x", "abort_session", "Abort", show=True),
        Binding("c", "cancel_session", "Cancel turn", show=True),
        Binding("e", "defer_approval", "Defer", show=True),
        Binding("A", "approve_group", "Approve group", show=True),
    ]

    CSS = """
    Screen { layout: vertical; }
    #status-bar {
        height: 1;
        background: $primary 60%;
        padding: 0 1;
        text-style: bold;
    }
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

    def __init__(self, poll_interval: float = 5.0) -> None:
        super().__init__()
        self._client = DaemonClient(default_socket_path())
        self._poll_interval = poll_interval
        self._approvals: list[dict[str, Any]] = []
        self._sessions: list[dict[str, Any]] = []
        self._selected_session_id: str | None = None
        self._graph_view: bool = False
        self._live_events: list[dict[str, Any]] = []
        self._onguard_summary: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status-bar")
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
        yield Static("Onguard Coordination", classes="pane-title")
        yield DataTable(id="onguard")
        yield Footer()

    def on_mount(self) -> None:
        sessions = self.query_one("#sessions", DataTable)
        sessions.add_columns("id", "status", "compartment", "intent", "labels")
        sessions.cursor_type = "row"

        events = self.query_one("#events", DataTable)
        events.add_columns("ts", "type", "session", "decision/labels")

        onguard = self.query_one("#onguard", DataTable)
        onguard.add_columns("client", "status", "queue", "schedules", "artifacts", "events")

        approvals = self.query_one("#approvals", DataTable)
        approvals.add_columns("id", "action", "target", "labels")
        approvals.cursor_type = "row"

        self.set_interval(self._poll_interval, self._refresh)
        self.refresh_now()
        self._start_event_stream()

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

    @work(exclusive=False)
    async def _start_event_stream(self) -> None:
        try:
            async for event in await self._client.subscribe(["audit"]):
                data = event.get("data") or {}
                self._live_events.append(data)
                if len(self._live_events) > 200:
                    self._live_events = self._live_events[-200:]
                self._update_events_table()
                triggers = {
                    "session.created",
                    "session.forked",
                    "session.aborted",
                    "session.paused",
                    "session.resumed",
                    "approval.requested",
                    "approval.approved",
                    "approval.denied",
                    "capability.granted",
                }
                if data.get("event_type") in triggers:
                    self.refresh_now()
        except Exception as e:
            self.notify(f"event stream error: {e}", severity="error")

    def _update_events_table(self) -> None:
        try:
            events_table = self.query_one("#events", DataTable)
        except Exception:
            return
        events_table.clear()
        for ev in reversed(self._live_events[-40:]):
            decision_or_labels = ""
            payload = ev.get("payload") or {}
            if ev.get("event_type") == "policy.decided":
                decision_or_labels = payload.get("decision", "")
            elif ev.get("event_type") == "label.propagated":
                decision_or_labels = "+" + ",".join(payload.get("labels_added", []))
            elif ev.get("event_type") == "approval.requested":
                decision_or_labels = f"approval #{payload.get('approval_id', '')}"
            elif ev.get("event_type") == "capability.granted":
                decision_or_labels = f"+{payload.get('kind', '')}"
            events_table.add_row(
                ev.get("timestamp", "")[11:19],
                ev.get("event_type", ""),
                (ev.get("session_id") or "")[:8],
                decision_or_labels,
            )

    @work(exclusive=True)
    async def _refresh(self) -> None:
        try:
            sessions_resp = await self._client.call("session.list", {})
            approvals_resp = await self._client.call(
                "approval.list",
                {"status": "pending"},
            )
            audit_resp = await self._client.call("audit.tail", {"limit": 50})
            onguard_summary = await self._load_onguard_summary()
        except DaemonNotRunningError:
            self.notify(
                "daemon not running — start it with `capdep daemon start`",
                severity="warning",
            )
            return

        self._sessions = sessions_resp["sessions"]
        self._onguard_summary = onguard_summary
        try:
            self._render_sessions()
            self._render_onguard()
        except Exception:
            return

        if not self._live_events:
            self._live_events = list(audit_resp["events"][-40:])
            self._update_events_table()

        try:
            approvals_table = self.query_one("#approvals", DataTable)
        except Exception:
            return
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

        await self._update_status_bar(audit_resp["events"])

    async def _load_onguard_summary(self) -> list[dict[str, Any]]:
        try:
            clients_resp = await self._client.call("client.registry.list", {"kind": "onguard"})
            queue_resp = await self._client.call("client.queue.list", {})
            schedules_resp = await self._client.call("schedule.list", {})
            artifacts_resp = await self._client.call("artifact.list", {})
            events_resp = await self._client.call("client.events.list", {"limit": 100})
        except Exception:
            return []

        by_client: dict[str, dict[str, Any]] = {}
        for client in clients_resp.get("clients", []):
            client_id = str(client.get("client_id") or "")
            if not client_id:
                continue
            by_client[client_id] = {
                "client_id": client_id,
                "status": client.get("status", ""),
                "queue": 0,
                "schedules": 0,
                "artifacts": 0,
                "events": 0,
            }

        for collection_name, key in (
            ("commands", "queue"),
            ("schedules", "schedules"),
            ("artifacts", "artifacts"),
            ("events", "events"),
        ):
            if collection_name == "commands":
                values = queue_resp.get(collection_name, [])
            elif collection_name == "schedules":
                values = schedules_resp.get(collection_name, [])
            elif collection_name == "artifacts":
                values = artifacts_resp.get(collection_name, [])
            else:
                values = events_resp.get(collection_name, [])
            for item in values:
                client_id = str(item.get("client_id") or "")
                if not client_id:
                    continue
                summary = by_client.setdefault(
                    client_id,
                    {
                        "client_id": client_id,
                        "status": "(unregistered)",
                        "queue": 0,
                        "schedules": 0,
                        "artifacts": 0,
                        "events": 0,
                    },
                )
                summary[key] += 1

        return sorted(by_client.values(), key=lambda row: row["client_id"])

    def _render_onguard(self) -> None:
        table = self.query_one("#onguard", DataTable)
        table.clear()
        for row in self._onguard_summary:
            table.add_row(
                row["client_id"],
                row["status"],
                str(row["queue"]),
                str(row["schedules"]),
                str(row["artifacts"]),
                str(row["events"]),
            )

    def _render_sessions(self) -> None:
        sessions_table = self.query_one("#sessions", DataTable)
        sessions_table.clear()

        def _cells(s: dict[str, Any], id_text: str) -> tuple[Any, ...]:
            labels = s.get("label_set", [])
            word, style = compartment_summary(labels)
            return (
                id_text,
                s["status"],
                Text(word, style=style),
                s["intent"] or "",
                Text.from_markup(render_labels(labels)),
            )

        if self._graph_view:
            by_parent: dict[str | None, list[dict[str, Any]]] = {}
            for s in self._sessions:
                by_parent.setdefault(s.get("parent"), []).append(s)

            def emit(parent_id: str | None, depth: int) -> None:
                for s in by_parent.get(parent_id, []):
                    indent = "  " * depth + ("└─ " if depth > 0 else "")
                    sessions_table.add_row(*_cells(s, indent + s["id"][:8]))
                    emit(s["id"], depth + 1)

            emit(None, 0)
        else:
            for s in self._sessions:
                sessions_table.add_row(*_cells(s, s["id"][:8]))

    async def _update_session_detail(
        self,
        session_id: str,
        recent_events: list[dict[str, Any]],
    ) -> None:
        try:
            full = await self._client.call("session.get", {"session_id": session_id})
        except Exception:
            return
        try:
            security_context = await self._client.call(
                "session.security_context",
                {"session_id": session_id},
            )
        except Exception:
            security_context = None

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
                # Surface the deterministic operator recovery for a
                # denied decision, the same hint the REPL shows.
                if t == "policy.decided" and payload.get("decision") == "deny":
                    hint = DENY_RECOVERY.get(payload.get("rule") or "")
                    if hint:
                        lines.append(f"  [cyan]↳ recover:[/cyan] [dim]{hint}[/dim]")
            trace_text = "\n".join(lines)

        # Prepend daemon-owned security context when available. Older
        # daemon/test doubles may only support session.get, so keep a
        # fallback but do not duplicate security derivation in the TUI.
        labels = (
            (security_context or {})
            .get("labels", {})
            .get("legacy_label_set", full.get("label_set", []))
        )
        word, style = compartment_summary(labels)
        header = [
            f"[bold]compartment[/bold] [{style}]{word}[/{style}]  {render_labels(labels)}",
        ]
        if security_context:
            session_ctx = security_context["session"]
            policy_ctx = security_context["policy"]
            approval_ctx = security_context["approvals"]
            provenance_ctx = security_context["provenance"]
            header.append(
                "[bold]security[/bold] "
                f"purpose={session_ctx['purpose_handle']} "
                f"enforcement={session_ctx['enforcement_mode']} "
                f"decisions={policy_ctx['decision_count']} "
                f"denies={policy_ctx['deny_count']} "
                f"approvals={approval_ctx['pending_count']} pending "
                f"provenance={provenance_ctx['node_count']}/{provenance_ctx['edge_count']}",
            )
        caps = (
            (security_context or {})
            .get("capabilities", {})
            .get("active", full.get("capability_set", []))
        )
        if caps:
            header.append(f"[bold]capabilities[/bold] ({len(caps)}):")
            header.extend(f"  - {capability_line(c)}" for c in caps)
        else:
            header.append("[bold]capabilities[/bold]: [dim]none[/dim]")
        header.append("")

        self.query_one("#trace", Static).update(
            "\n".join(header) + trace_text,
        )

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

    def action_pause_session(self) -> None:
        self._run_selected_session_rpc("session.pause", "paused")

    def action_resume_session(self) -> None:
        self._run_selected_session_rpc("session.resume", "resumed")

    def action_abort_session(self) -> None:
        self._run_selected_session_rpc("session.abort", "aborted")

    def action_cancel_session(self) -> None:
        self._run_selected_session_rpc("session.cancel", "cancel requested")

    def _run_selected_session_rpc(self, method: str, message: str) -> None:
        if not self._selected_session_id:
            self.notify("select a session first", severity="warning")
            return
        self.run_worker(
            self._call_session_rpc(method, self._selected_session_id, message),
            exclusive=False,
        )

    async def _call_session_rpc(self, method: str, session_id: str, message: str) -> None:
        try:
            await self._client.call(method, {"session_id": session_id})
        except Exception as e:
            self.notify(f"{method} failed: {e}", severity="error")
            return
        self.notify(f"{message}: {session_id[:8]}")
        self.refresh_now()

    def action_defer_approval(self) -> None:
        chosen = self._selected_approval()
        if chosen is None:
            return
        self.run_worker(self._call_approval_rpc("approval.defer", chosen["id"], "deferred"))

    def action_approve_group(self) -> None:
        chosen = self._selected_approval()
        if chosen is None:
            return
        group_id = chosen.get("sibling_group_id")
        if not group_id:
            self.notify("selected approval has no sibling group", severity="warning")
            return
        self.run_worker(self._approve_group(str(group_id)))

    def _selected_approval(self) -> dict[str, Any] | None:
        approvals_table = self.query_one("#approvals", DataTable)
        if not approvals_table.is_valid_row_index(approvals_table.cursor_row):
            self.notify("select an approval first", severity="warning")
            return None
        if approvals_table.cursor_row >= len(self._approvals):
            self.notify("select an approval first", severity="warning")
            return None
        return self._approvals[approvals_table.cursor_row]

    async def _call_approval_rpc(self, method: str, approval_id: int, message: str) -> None:
        try:
            await self._client.call(method, {"id": approval_id})
        except Exception as e:
            self.notify(f"{method} failed: {e}", severity="error")
            return
        self.notify(f"{message}: approval #{approval_id}")
        self.refresh_now()

    async def _approve_group(self, group_id: str) -> None:
        try:
            await self._client.call("approval.approve_group", {"group_id": group_id})
        except Exception as e:
            self.notify(f"approval.approve_group failed: {e}", severity="error")
            return
        self.notify(f"approved group {group_id[:8]}")
        self.refresh_now()

    async def _update_status_bar(self, recent_events: list[dict[str, Any]]) -> None:
        """Refresh the always-visible status bar at the top of the TUI.

        Surfaces operator-level state that the panes don't otherwise
        expose at a glance: active session compartment, pending
        approvals + overrides, count of recent denials. Designed for
        situational awareness — "what should I be paying attention to
        right now?"
        """
        try:
            override_resp = await self._client.call("override.list", {})
            grants = override_resp.get("grants", [])
        except Exception:
            grants = []

        # Active overrides: those in pending_attestation or active states
        pending_overrides = sum(
            1 for g in grants if g.get("state") in ("pending_attestation", "active")
        )

        # Recent denials in the last ~40 events
        denials = sum(
            1
            for e in recent_events[-40:]
            if e.get("event_type") == "policy.decided"
            and (e.get("payload") or {}).get("decision") == "deny"
        )

        active_sessions = sum(1 for s in self._sessions if s["status"] == "active")
        pending_approvals = len(self._approvals)

        # Selected session compartment, if any
        compartment_part = ""
        if self._selected_session_id:
            for s in self._sessions:
                if s["id"] == self._selected_session_id:
                    word, _style = compartment_summary(s.get("label_set", []))
                    compartment_part = f"  selected: {s['id'][:8]} [{word}]"
                    break

        parts = [
            f"sessions {active_sessions}",
            f"pending approvals [bold yellow]{pending_approvals}[/bold yellow]"
            if pending_approvals
            else "pending approvals 0",
            f"overrides [bold cyan]{pending_overrides}[/bold cyan]"
            if pending_overrides
            else "overrides 0",
            f"recent denials [bold red]{denials}[/bold red]" if denials else "recent denials 0",
        ]
        import contextlib

        text = "  ·  ".join(parts) + compartment_part

        with contextlib.suppress(Exception):
            self.query_one("#status-bar", Static).update(text)

    def action_list_overrides(self) -> None:
        """List active and pending override grants in a notification.

        For now, a lightweight summary; a full modal could be added
        later. The CLI (`capdep override list`) remains the primary
        management surface.
        """
        self.run_worker(self._show_overrides())

    async def _show_overrides(self) -> None:
        try:
            resp = await self._client.call("override.list", {})
        except Exception as e:
            self.notify(f"override.list failed: {e}", severity="error")
            return
        grants = resp.get("grants", [])
        if not grants:
            self.notify("no override grants in store", severity="information")
            return
        active = [g for g in grants if g.get("state") == "active"]
        pending = [g for g in grants if g.get("state") == "pending_attestation"]
        consumed = [g for g in grants if g.get("state") == "consumed"]
        lines = []
        if pending:
            lines.append(f"{len(pending)} pending attestation")
        if active:
            lines.append(f"{len(active)} active")
        if consumed:
            lines.append(f"{len(consumed)} consumed")
        self.notify(
            "Override grants: " + ", ".join(lines) + "\n(use `capdep override list` for details)",
            severity="information",
            timeout=8,
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
