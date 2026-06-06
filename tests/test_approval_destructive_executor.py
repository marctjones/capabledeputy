"""End-to-end: a memory.update REQUIRE_APPROVAL gate gets submitted,
approved, and the update *actually executes* via a purpose-limited
session with allows_destructive=True. Closes the gap where
destructive-op approvals previously only toggled status.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier


@pytest.fixture
async def app(tmp_path: Path) -> App:
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await a.startup()
    return a


async def test_destructive_approval_actually_executes_memory_update(
    app: App,
) -> None:
    # 1. Seed a memory key the destructive op will mutate.
    app.memory.write(
        "note-1",
        "old",
        LabelState(
            a=frozenset(
                {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )

    # 2. Origin session — has the WRITE_FS cap but NOT allows_destructive,
    #    so memory.update would return REQUIRE_APPROVAL via the destructive-op gate.
    origin = await app.graph.new(intent="origin")
    cap = Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        allows_destructive=False,
    )
    app.graph._sessions[origin.id] = replace(
        origin,
        capability_set=frozenset({cap}),
    )

    handlers = make_approval_handlers(app)

    # 3. Submit the approval as the REPL would on auto-submit.
    submit_result = await handlers["approval.submit"](
        {
            "from_session": str(origin.id),
            "action": ApprovalAction.EXECUTE_DESTRUCTIVE.value,
            "target": "note-1",
            "payload": json.dumps(
                {
                    "tool": "memory.update",
                    "args": {"key": "note-1", "value": "new"},
                },
            ),
            "justification": "test",
        },
    )
    approval_id = submit_result["id"]

    # 4. Approve. This must dispatch the destructive op in a purpose
    #    session and actually mutate memory.
    approve_result = await handlers["approval.approve"]({"id": approval_id})

    assert approve_result["dispatch"]["decision"] == "allow"
    assert approve_result["executed_in_session"] is not None

    # 5. The actual store state must have changed.
    after = app.memory.read("note-1")
    assert after is not None
    assert after.value == "new"


async def test_destructive_approval_calendar_delete_executes(app: App) -> None:
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from capabledeputy.tools.native.calendar import CalendarEvent

    ev_id = uuid4()
    now = datetime.now(UTC)
    app.calendar.add(
        CalendarEvent(
            id=ev_id,
            title="To be deleted",
            starts_at=now,
            ends_at=now + timedelta(minutes=30),
        ),
    )
    assert app.calendar.get(ev_id) is not None

    origin = await app.graph.new(intent="origin")
    cap = Capability(
        kind=CapabilityKind.CALENDAR_WRITE,
        pattern="*",
        allows_destructive=False,
    )
    app.graph._sessions[origin.id] = replace(
        origin,
        capability_set=frozenset({cap}),
    )

    handlers = make_approval_handlers(app)
    submit_result = await handlers["approval.submit"](
        {
            "from_session": str(origin.id),
            "action": ApprovalAction.EXECUTE_DESTRUCTIVE.value,
            "target": str(ev_id),
            "payload": json.dumps(
                {
                    "tool": "calendar.delete_event",
                    "args": {"id": str(ev_id)},
                },
            ),
            "justification": "test",
        },
    )
    await handlers["approval.approve"]({"id": submit_result["id"]})

    assert app.calendar.get(ev_id) is None


async def test_destructive_approval_malformed_payload_returns_error(
    app: App,
) -> None:
    origin = await app.graph.new(intent="origin")
    handlers = make_approval_handlers(app)
    submit_result = await handlers["approval.submit"](
        {
            "from_session": str(origin.id),
            "action": ApprovalAction.EXECUTE_DESTRUCTIVE.value,
            "target": "x",
            "payload": "not-json",
            "justification": "t",
        },
    )
    result = await handlers["approval.approve"]({"id": submit_result["id"]})
    assert result["dispatch"]["decision"] == "deny"
    assert "malformed" in (result["dispatch"].get("reason") or "")
