"""The inline console app (TUI redesign §3 / §7).

A Textual inline conversational REPL: a fixed engine-sourced status line, a
streaming conversation log, and an input. Decisions render inline as cards, but
the *interaction* is armed — a keypress (`a`/`d`/`o`) only ever resolves the one
decision the app has armed (a painted fake card in untrusted content is inert,
because there is no other path to "approve"). Grave actions
(override / prohibited) escalate to a focused confirm that requires typing the
engine-provided target. A global kill switch halts the session from the keyboard.

A `ConsoleDriver` feeds the conversation (a real one wires to the daemon agent
loop; the demo driver in `demo.py` scripts a showcase). The driver calls the
app's view methods and `await`s `request_decision` for any gated action.
"""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID, uuid4

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, RichLog, Static

from capabledeputy.policy.rules import Decision
from capabledeputy.tui.inline.decision import marker_for_session
from capabledeputy.tui.inline.model import ApprovalPrompt, Entry, Outcome, render_entry
from capabledeputy.tui.inline.status import TrustState, render_status


class ConsoleDriver(Protocol):
    """Feeds one turn into the console. Calls the view methods on `console`
    and awaits `console.request_decision(...)` for any gated action."""

    async def run_turn(self, text: str, console: InlineConsole) -> None: ...


class OverrideConfirmScreen(ModalScreen[bool]):
    """Grave-action escalation (§8.1 #6): the human must type the engine-
    provided target to confirm. The target comes from chrome, never echoed
    from model text."""

    DEFAULT_CSS = """
    OverrideConfirmScreen { align: center middle; }
    #box { width: 70; height: auto; border: thick $warning; padding: 1 2; background: $surface; }
    #title { text-style: bold; color: $warning; }
    """

    def __init__(self, target: str) -> None:
        super().__init__()
        self._target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("⚖ override — type the target to confirm", id="title")
            yield Static(f"target: {self._target}")
            yield Input(placeholder="type the exact target…", id="confirm")

    @on(Input.Submitted, "#confirm")
    def _submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() == self._target)


class InlineConsole(App[None]):
    CSS = """
    Screen { layout: vertical; }
    #status { dock: top; height: 1; color: $text-muted; background: $panel; padding: 0 1; }
    #log { height: 1fr; padding: 0 1; }
    #input { dock: bottom; border: none; padding: 0 1; }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("ctrl+c", "quit", "quit"),
        Binding("ctrl+k", "halt", "halt session"),
        Binding("a", "approve", "approve", show=False),
        Binding("d", "deny", "deny", show=False),
        Binding("o", "override", "override", show=False),
        Binding("w", "why", "why", show=False),
    ]

    def __init__(
        self,
        driver: ConsoleDriver,
        *,
        trust: TrustState | None = None,
        session_id: UUID | None = None,
    ) -> None:
        super().__init__()
        self._turn_driver = driver  # NB: `_driver` is reserved by Textual
        sid = session_id or uuid4()
        self._marker = marker_for_session(sid)
        self._trust = trust or TrustState(session_name="session")
        self._armed: ApprovalPrompt | None = None
        self._armed_future: asyncio.Future[str] | None = None

    # --- layout ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        yield Input(id="input", placeholder="message… (/ for commands)")

    def on_mount(self) -> None:
        self._refresh_status()
        self.query_one("#input", Input).focus()

    # --- view methods the driver calls ---------------------------------

    def append(self, entry: Entry) -> None:
        self.query_one("#log", RichLog).write(render_entry(entry, marker=self._marker))

    def set_trust(self, trust: TrustState) -> None:
        self._trust = trust
        self._refresh_status()

    async def request_decision(self, prompt: ApprovalPrompt) -> str:
        """Arm `prompt` and wait for the human. Returns 'approve' | 'deny' |
        'override'. While armed the input is disabled so the single-key
        decision bindings are live; a painted fake card is inert because this
        is the *only* path that resolves a decision."""
        self.append(prompt)
        self._armed = prompt
        self._armed_future = asyncio.get_event_loop().create_future()
        inp = self.query_one("#input", Input)
        inp.disabled = True
        self.set_focus(None)
        try:
            return await self._armed_future
        finally:
            self._armed = None
            self._armed_future = None
            inp.disabled = False
            inp.focus()

    def _refresh_status(self) -> None:
        self._last_status = render_status(self._trust, self._marker)
        self.query_one("#status", Static).update(self._last_status)

    def _resolve(self, choice: str, outcome: str, style: str) -> None:
        fut = self._armed_future
        if fut is None or fut.done():
            return  # inert: no armed decision
        self.append(Outcome(outcome, style))
        fut.set_result(choice)

    # --- key actions (inert unless a decision is armed) ----------------

    def action_approve(self) -> None:
        if self._armed and self._armed.decision.decision is Decision.REQUIRE_APPROVAL:
            self._resolve("approve", "✓ approved", "green")

    def action_deny(self) -> None:
        if self._armed:
            self._resolve("deny", "✗ denied", "red")

    @work
    async def action_override(self) -> None:
        if not self._armed or self._armed.decision.decision is not Decision.OVERRIDE_REQUIRED:
            return
        target = self._armed.target
        confirmed = await self.push_screen_wait(OverrideConfirmScreen(target))
        if confirmed:
            self._resolve("override", "⚖ overridden", "magenta")

    def action_why(self) -> None:
        if self._armed and self._armed.decision.reason:
            self.append(Outcome(f"  why: {self._armed.decision.reason}", "dim"))

    def action_halt(self) -> None:
        """Kill switch (§8.1 #7): halt the session from the keyboard."""
        self.append(Outcome("■ session halted by operator", "bold red"))
        if self._armed_future and not self._armed_future.done():
            self._resolve("deny", "✗ denied (halt)", "red")

    # --- input ----------------------------------------------------------

    @on(Input.Submitted, "#input")
    def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        from capabledeputy.tui.inline.model import UserMessage

        self.append(UserMessage(text))
        self._drive(text)

    @work
    async def _drive(self, text: str) -> None:
        await self._turn_driver.run_turn(text, self)
