"""End-to-end: time-bounded capabilities through the policy chokepoint.

US1 acceptance + SC-005 (audit attribution) + SC-006 (LLM isolation).
Deterministic: no real LLM; expiry is forced by granting a cap whose
absolute deadline is already in the past at dispatch time.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.audit.events import EventType
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.tools.client import LabeledToolClient


@pytest.fixture
async def app(tmp_path: Path) -> App:
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await a.startup()
    return a


async def _read_session_with_cap(app: App, cap: Capability):
    s = await app.graph.new()
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    return s


async def test_future_deadline_allows_then_past_deadline_denies(app: App) -> None:
    now = datetime.now(UTC)

    # Future deadline → normal ALLOW (C2).
    s1 = await _read_session_with_cap(
        app,
        Capability(
            kind=CapabilityKind.READ_FS,
            pattern="*",
            expires_at=now + timedelta(hours=1),
        ),
    )
    before = await app.tool_client.call_tool(
        s1.id, "memory.read", {"key": "anything"},
    )
    assert before.decision.value == "allow"

    # Past deadline → DENY attributed to expiry (C3/C4, SC-001).
    s2 = await _read_session_with_cap(
        app,
        Capability(
            kind=CapabilityKind.READ_FS,
            pattern="*",
            expires_at=now - timedelta(seconds=1),
        ),
    )
    after = await app.tool_client.call_tool(
        s2.id, "memory.read", {"key": "anything"},
    )
    assert after.decision.value == "deny"
    assert after.rule == "capability-expired"
    assert "expired at" in (after.reason or "")


async def test_non_expired_sibling_survives_e2e(app: App) -> None:
    now = datetime.now(UTC)
    s = await app.graph.new()
    expired = Capability(
        kind=CapabilityKind.READ_FS, pattern="*",
        expires_at=now - timedelta(seconds=1),
    )
    live = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(
        s, capability_set=frozenset({expired, live}),
    )
    outcome = await app.tool_client.call_tool(
        s.id, "memory.read", {"key": "k"},
    )
    assert outcome.decision.value == "allow"


async def test_expiry_denial_recorded_in_audit_trail(app: App) -> None:
    """SC-005 / FR-010: the persisted POLICY_DECIDED event must let an
    auditor reconstruct, from the trail alone, that the action was
    denied because a matching capability's deadline had passed —
    distinct from a no-capability denial."""
    now = datetime.now(UTC)
    s = await _read_session_with_cap(
        app,
        Capability(
            kind=CapabilityKind.READ_FS,
            pattern="*",
            expires_at=now - timedelta(seconds=5),
        ),
    )
    await app.tool_client.call_tool(s.id, "memory.read", {"key": "k"})

    events = await app.audit.read_all()
    decided = [
        e for e in events
        if e.event_type == EventType.POLICY_DECIDED
        and e.session_id == s.id
    ]
    assert decided, "expected a POLICY_DECIDED audit event"
    payload = decided[-1].payload
    assert payload["decision"] == "deny"
    assert payload["rule"] == "capability-expired"
    assert "expired at" in (payload["reason"] or "")  # the deadline


async def test_sc006_invariant_identical_with_preview_disabled(
    tmp_path: Path,
) -> None:
    """SC-006: expiry enforcement is byte-identical whether or not the
    policy.preview tool exists, and no LLM client is on the path."""
    now = datetime.now(UTC)
    cap = Capability(
        kind=CapabilityKind.READ_FS, pattern="*",
        expires_at=now - timedelta(seconds=1),
    )

    results = {}
    for preview in (True, False):
        a = App(
            state_db_path=tmp_path / f"s-{preview}.db",
            audit_log_path=tmp_path / f"a-{preview}.jsonl",
            enable_policy_preview=preview,
        )
        await a.startup()
        assert a.llm_client is None  # no LLM anywhere on this path
        s = await a.graph.new()
        a.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
        o = await a.tool_client.call_tool(s.id, "memory.read", {"key": "k"})
        results[preview] = (o.decision.value, o.rule)

    assert results[True] == results[False] == ("deny", "capability-expired")


# Suppress unused-import warning for the fixture-only LabeledToolClient
# reference (kept for readers tracing the chokepoint).
_ = LabeledToolClient
