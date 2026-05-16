"""Demo 06 — recurring purchase via approval pattern library.

The user has a standing arrangement: weekly grocery orders at a
specific vendor are pre-approved, but every approval still emits the
full APPROVAL_REQUESTED + APPROVAL_APPROVED audit pair so nothing is
silent. Off-pattern purchases (different vendor) do NOT auto-approve.

Workflow:
  1. Import `configs/approval-patterns.yaml` — registers the
     groceries-amazon-fresh pattern (target = `amazon-fresh`).
  2. Submit a SEND_EMAIL approval matching the pattern (the demo uses
     SEND_EMAIL because the pattern library example covers it; the
     same mechanism applies to QUEUE_PURCHASE). It auto-approves on
     submit and the matched_rule is recorded.
  3. Submit a similar approval to a vendor that isn't on the pattern
     and watch it stay PENDING.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from capabledeputy.app import App
from capabledeputy.approval.library import (
    apply_library,
    load_library_file,
)
from capabledeputy.approval.model import ApprovalAction


async def test_pattern_library_auto_approves_matching_submission(
    tmp_path: Path,
) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()

    library_path = Path(__file__).parent.parent / "configs" / "approval-patterns.yaml"
    entries = load_library_file(library_path)
    rules = apply_library(entries, app.approval_queue.patterns)
    assert len(rules) >= 1

    # Submit a SEND_EMAIL request that matches the spouse-prescription
    # pattern (target=spouse@example.com, payload contains
    # "prescription"). Should auto-approve.
    matching = await app.approval_queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload="Updated prescription summary attached.",
        target="spouse@example.com",
        labels_in=frozenset(),
        justification="recurring family update",
    )
    assert matching.status.value == "approved"
    assert matching.decided_by is not None
    assert matching.decided_by.startswith("pattern:")

    # Submit a SEND_EMAIL to someone NOT in the pattern: should stay
    # pending. Auto-approval must be specific by design.
    pending = await app.approval_queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload="hi",
        target="random-stranger@example.com",
        labels_in=frozenset(),
    )
    assert pending.status.value == "pending"

    # Audit pair fired even for the auto-approved one — no silent passes.
    events = await app.audit.read_all()
    types = [e.event_type.value for e in events]
    assert "approval.requested" in types
    assert "approval.approved" in types
    matched_approvals = [
        e
        for e in events
        if e.event_type.value == "approval.approved"
        and e.payload.get("decision_scope", {}).get("matched_rule")
    ]
    assert len(matched_approvals) >= 1
