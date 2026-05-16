"""Layer-4 visual regression: a single, deterministic SVG snapshot of
the verbatim-approval modal — the most security-critical screen (the
human reviews exactly what will happen here).

Deliberately scoped, per the agreed constraints:
  - Only the ApprovalDetailScreen (a ModalScreen with NO Header clock
    and NO time-relative text) → the rendered frame is fully
    determined by a fixed approval dict. Full-app snapshots would be
    flaky (live Header clock, "expires Ns" sidebar) — excluded on
    purpose.
  - Fixed terminal_size.
  - Marked `snapshot` so a Textual bump churning the baseline can be
    isolated: run the rest with `-m "not snapshot"`; regenerate with
    `uv run pytest -m snapshot --snapshot-update` and visually
    re-review the diff before committing.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from capabledeputy.tui.app import ApprovalDetailScreen

_FIXED_APPROVAL = {
    "id": 7,
    "action": "SEND_EMAIL",
    "status": "pending",
    "target": "accountant@example.com",
    "from_session": "0d978043-1b53-4466-9897-87b346591561",
    "labels_in": ["confidential.financial"],
    "justification": "agent-initiated email.send",
    "payload": "May spending summary: rent 2400, groceries 720, utilities 145.",
}


class _ModalApp(App[None]):
    """Minimal host whose only screen is the approval modal — nothing
    time-varying, so the snapshot is byte-stable."""

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(ApprovalDetailScreen(_FIXED_APPROVAL))


@pytest.mark.snapshot
def test_approval_modal_snapshot(snap_compare) -> None:
    assert snap_compare(_ModalApp(), terminal_size=(100, 30))
