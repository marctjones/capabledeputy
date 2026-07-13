"""v0.55 viability probe (#300/#301/#303) — the memory-ingest → memory.handle →
route flow under REFERENCE mode. Proves restricted-tier sensitive data can be
USED (routed to a destination) without the planner ever holding the raw value,
instead of the turn being refused.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.mode.dispatcher import ExecutionMode, select_mode
from capabledeputy.patterns.reference_handle import ReferenceHandleStore
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier


def _restricted_labels() -> LabelState:
    return LabelState(a=frozenset({CategoryTag("financial", Tier.RESTRICTED)}))


def test_restricted_session_selects_reference_not_refusal() -> None:
    """#300/#301: with a handle-aware routing tool (fs.create) in the default
    native surface, a restricted-tier session selects REFERENCE instead of
    raising ModeSelectionError (the pre-v0.55 refusal)."""
    import asyncio

    async def _run() -> None:
        import tempfile

        d = Path(tempfile.mkdtemp())
        app = App(state_db_path=d / "s.db", audit_log_path=d / "a.jsonl")
        await app.startup()
        mode, reason = select_mode(
            registry=app.registry,
            label_state=_restricted_labels(),
            session=None,
        )
        assert mode == ExecutionMode.REFERENCE, reason

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_restricted_value_routed_via_handle_never_seen_by_planner(
    tmp_path: Path,
) -> None:
    """End-to-end: a restricted value in labeled memory is routed to a file via a
    Pattern-3 handle. The handler writes the REAL value; the planner-supplied
    args only ever contained the opaque handle UUID, never the raw value."""
    store = ReferenceHandleStore()
    app = App(
        state_db_path=tmp_path / "s.db",
        audit_log_path=tmp_path / "a.jsonl",
        policy_context=PolicyContext(handle_store=store),
    )
    await app.startup()

    secret = "ACCOUNT 12345 BALANCE $9,001"
    app.memory.write("stmt", secret, _restricted_labels())

    s = await app.graph.new(intent="file my statement")
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset(
            {
                Capability(kind=CapabilityKind.READ_FS, pattern="*"),
                Capability(kind=CapabilityKind.CREATE_FS, pattern="*"),
            }
        ),
    )

    # 1. Planner obtains an opaque handle for the restricted value (never reads it).
    h = await app.tool_client.call_tool(s.id, "memory.handle", {"key": "stmt"})
    assert h.decision == Decision.ALLOW
    assert h.output is not None
    handle_id = h.output["handle"]
    assert secret not in handle_id  # planner sees a UUID, not the value

    # 2. Planner routes the handle into fs.create's content arg. The value the
    #    planner supplies is the HANDLE, not the secret.
    dest = tmp_path / "filed_statement.txt"
    planner_args = {"path": str(dest), "content": handle_id}
    assert secret not in str(planner_args)  # the raw value is nowhere in planner args

    outcome = await app.tool_client.call_tool(s.id, "fs.create", planner_args)
    assert outcome.decision == Decision.ALLOW, outcome.reason

    # 3. The FILE received the real value (bound post-decide), proving the route
    #    worked — the planner routed data it never held.
    assert dest.read_text() == secret
