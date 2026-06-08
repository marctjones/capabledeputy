"""The `ConsoleView` protocol — the seam that makes the console automatable.

A `ConsoleDriver` drives a turn by calling these three methods. Two things
implement the protocol:
- `InlineConsole` (app.py) — the real Textual UI.
- `HeadlessConsole` (harness.py) — a no-terminal recorder for automated scripts.

Because the driver depends only on this protocol, the SAME driver code runs
unchanged against the real UI or the headless recorder. That is what lets one
scenario script test the server (via a real driver) and the UI (via Pilot)
from the same description.
"""

from __future__ import annotations

from typing import Protocol

from capabledeputy.tui.inline.model import ApprovalPrompt, Entry
from capabledeputy.tui.inline.status import TrustState


class ConsoleView(Protocol):
    def append(self, entry: Entry) -> None: ...

    def set_trust(self, trust: TrustState) -> None: ...

    async def request_decision(self, prompt: ApprovalPrompt) -> str: ...
