"""Cookbook Pattern ⑥ — shadow mode end-to-end.

Tests cover:
  - Session.enforcement_mode round-trips through to_dict/from_dict
  - default-tolerant load: a session without the field deserializes
    as STRICT
  - SessionGraph.set_enforcement_mode emits ENFORCEMENT_MODE_CHANGED
  - shadow rewrite turns DENY into ALLOW and emits POLICY_SHADOWED
  - shadow rewrite leaves ALLOW alone
  - shadow does NOT rewrite "no matching capability" denies (those
    are structural, not rule-driven)
  - flipping back to STRICT restores normal denial
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.model import EnforcementMode, Session

# --- Model -----------------------------------------------------------------


def test_session_default_enforcement_is_strict() -> None:
    s = Session.new()
    assert s.enforcement_mode == EnforcementMode.STRICT


def test_session_dict_round_trip_preserves_shadow() -> None:
    from dataclasses import replace

    s = replace(Session.new(), enforcement_mode=EnforcementMode.SHADOW)
    d = s.to_dict()
    assert d["enforcement_mode"] == "shadow"
    s2 = Session.from_dict(d)
    assert s2.enforcement_mode == EnforcementMode.SHADOW


def test_session_from_dict_defaults_to_strict_for_legacy() -> None:
    """Pre-Pattern-⑥ sessions in the state DB don't carry the field.
    from_dict must default to STRICT so existing sessions keep
    behaving as before."""
    s = Session.new()
    d = s.to_dict()
    d.pop("enforcement_mode")  # simulate legacy row
    s2 = Session.from_dict(d)
    assert s2.enforcement_mode == EnforcementMode.STRICT


# --- SessionGraph.set_enforcement_mode -------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_set_enforcement_mode_emits_audit(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    writer = AuditWriter(audit_path)
    graph = SessionGraph(audit=writer)
    s = await graph.new(intent="test")
    assert s.enforcement_mode == EnforcementMode.STRICT

    s2 = await graph.set_enforcement_mode(s.id, EnforcementMode.SHADOW)
    assert s2.enforcement_mode == EnforcementMode.SHADOW

    events = [
        json.loads(line)
        for line in audit_path.read_text().splitlines()
        if "enforcement.mode_changed" in line
    ]
    assert len(events) == 1
    assert events[0]["payload"]["old_mode"] == "strict"
    assert events[0]["payload"]["new_mode"] == "shadow"


@pytest.mark.anyio
async def test_set_enforcement_mode_is_noop_when_already_set(
    tmp_path: Path,
) -> None:
    """Flipping to the same mode doesn't emit a second audit event —
    nothing changed."""
    audit_path = tmp_path / "audit.jsonl"
    writer = AuditWriter(audit_path)
    graph = SessionGraph(audit=writer)
    s = await graph.new()
    await graph.set_enforcement_mode(s.id, EnforcementMode.SHADOW)
    await graph.set_enforcement_mode(s.id, EnforcementMode.SHADOW)
    events = [
        line for line in audit_path.read_text().splitlines() if "enforcement.mode_changed" in line
    ]
    assert len(events) == 1  # only the first change


# --- Shadow rewrite at the dispatcher --------------------------------------


@pytest.mark.anyio
async def test_shadow_rewrite_turns_deny_into_allow(tmp_path: Path) -> None:
    """The full shadow-rewrite path through the tool client. A
    session in SHADOW mode with a DENY-bound rule sees ALLOW
    delivered and POLICY_SHADOWED in the audit log."""
    from dataclasses import dataclass

    from capabledeputy.policy.rules import Decision
    from capabledeputy.tools.client import LabeledToolClient, PolicyContext

    @dataclass
    class _FakeDecision:
        decision: Decision
        rule: str | None
        reason: str | None = None
        effective_labels: frozenset = frozenset()  # type: ignore[type-arg]

    @dataclass
    class _FakeAction:
        target: str = "x@example.com"

    @dataclass
    class _FakeSession:
        enforcement_mode: EnforcementMode

    audit_path = tmp_path / "audit.jsonl"
    writer = AuditWriter(audit_path)
    client = LabeledToolClient(
        registry=None,  # type: ignore[arg-type]
        graph=None,
        audit=writer,
        policy_context=PolicyContext(),
    )
    proposed = _FakeDecision(
        decision=Decision.DENY,
        rule="untrusted-meets-egress",
        reason="untrusted.* + egress.* denied",
    )
    session = _FakeSession(enforcement_mode=EnforcementMode.SHADOW)
    result = await client._maybe_shadow_rewrite(
        uuid4(),
        session,
        "fake_tool",
        _FakeAction(),
        proposed,
    )
    assert result.decision == Decision.ALLOW
    assert "shadowed" in (result.reason or "").lower()
    # POLICY_SHADOWED event emitted with the original decision
    events = [
        json.loads(line)
        for line in audit_path.read_text().splitlines()
        if "policy.shadowed" in line
    ]
    assert len(events) == 1
    assert events[0]["payload"]["would_be_decision"] == "deny"
    assert events[0]["payload"]["rule"] == "untrusted-meets-egress"


@pytest.mark.anyio
async def test_shadow_does_not_rewrite_allow(tmp_path: Path) -> None:
    """ALLOW outcomes pass through unchanged in shadow mode — no
    POLICY_SHADOWED noise for normal operations."""
    from dataclasses import dataclass

    from capabledeputy.policy.rules import Decision
    from capabledeputy.tools.client import LabeledToolClient, PolicyContext

    @dataclass
    class _FakeDecision:
        decision: Decision
        rule: str | None
        reason: str | None = None
        effective_labels: frozenset = frozenset()  # type: ignore[type-arg]

    @dataclass
    class _FakeAction:
        target: str = "x@example.com"

    @dataclass
    class _FakeSession:
        enforcement_mode: EnforcementMode

    audit_path = tmp_path / "audit.jsonl"
    writer = AuditWriter(audit_path)
    client = LabeledToolClient(
        registry=None,  # type: ignore[arg-type]
        graph=None,
        audit=writer,
        policy_context=PolicyContext(),
    )
    proposed = _FakeDecision(
        decision=Decision.ALLOW,
        rule="some-allow-rule",
    )
    session = _FakeSession(enforcement_mode=EnforcementMode.SHADOW)
    result = await client._maybe_shadow_rewrite(
        uuid4(),
        session,
        "fake_tool",
        _FakeAction(),
        proposed,
    )
    assert result is proposed  # unchanged
    # No POLICY_SHADOWED audit emitted — audit file may not exist
    # yet (no event of any type fired).
    text = audit_path.read_text() if audit_path.exists() else ""
    events = [line for line in text.splitlines() if "policy.shadowed" in line]
    assert events == []  # no audit noise


@pytest.mark.anyio
async def test_shadow_does_not_bypass_capability_structural_deny(
    tmp_path: Path,
) -> None:
    """A 'no matching capability' deny is a STRUCTURAL check — the
    session doesn't hold the authority. Shadow mode is for rule
    outcome validation; it must NOT silently grant missing-cap
    authority."""
    from dataclasses import dataclass

    from capabledeputy.policy.rules import Decision
    from capabledeputy.tools.client import LabeledToolClient, PolicyContext

    @dataclass
    class _FakeDecision:
        decision: Decision
        rule: str | None
        reason: str | None = None
        effective_labels: frozenset = frozenset()  # type: ignore[type-arg]

    @dataclass
    class _FakeAction:
        target: str = "x@example.com"

    @dataclass
    class _FakeSession:
        enforcement_mode: EnforcementMode

    writer = AuditWriter(tmp_path / "audit.jsonl")
    client = LabeledToolClient(
        registry=None,  # type: ignore[arg-type]
        graph=None,
        audit=writer,
        policy_context=PolicyContext(),
    )
    proposed = _FakeDecision(
        decision=Decision.DENY,
        rule=None,
        reason="no matching capability for SEND_EMAIL(x@example.com)",
    )
    session = _FakeSession(enforcement_mode=EnforcementMode.SHADOW)
    result = await client._maybe_shadow_rewrite(
        uuid4(),
        session,
        "fake_tool",
        _FakeAction(),
        proposed,
    )
    assert result is proposed  # unchanged — structural deny survives
    assert result.decision == Decision.DENY


@pytest.mark.anyio
async def test_strict_mode_does_not_rewrite(tmp_path: Path) -> None:
    """STRICT mode is the back-compat default — non-ALLOW outcomes
    must NOT be rewritten."""
    from dataclasses import dataclass

    from capabledeputy.policy.rules import Decision
    from capabledeputy.tools.client import LabeledToolClient, PolicyContext

    @dataclass
    class _FakeDecision:
        decision: Decision
        rule: str | None
        reason: str | None = None
        effective_labels: frozenset = frozenset()  # type: ignore[type-arg]

    @dataclass
    class _FakeAction:
        target: str = "x@example.com"

    @dataclass
    class _FakeSession:
        enforcement_mode: EnforcementMode

    writer = AuditWriter(tmp_path / "audit.jsonl")
    client = LabeledToolClient(
        registry=None,  # type: ignore[arg-type]
        graph=None,
        audit=writer,
        policy_context=PolicyContext(),
    )
    proposed = _FakeDecision(
        decision=Decision.DENY,
        rule="some-rule",
        reason="rule-driven deny",
    )
    session = _FakeSession(enforcement_mode=EnforcementMode.STRICT)
    result = await client._maybe_shadow_rewrite(
        uuid4(),
        session,
        "fake_tool",
        _FakeAction(),
        proposed,
    )
    assert result is proposed


# --- Persistence -----------------------------------------------------------


@pytest.mark.anyio
async def test_enforcement_persists_across_reload(tmp_path: Path) -> None:
    """A session set to SHADOW saves to the state DB and reloads as
    SHADOW. No mode flip is silent across restart."""
    from capabledeputy.session.store import SessionStore

    audit_path = tmp_path / "audit.jsonl"
    db_path = tmp_path / "state.db"
    store = SessionStore(db_path)
    await store.initialize()
    writer = AuditWriter(audit_path)
    graph = SessionGraph(audit=writer, store=store)
    s = await graph.new(intent="persist-test")
    await graph.set_enforcement_mode(s.id, EnforcementMode.SHADOW)

    # New graph + store → reload from disk
    store2 = SessionStore(db_path)
    await store2.initialize()
    graph2 = SessionGraph(audit=writer, store=store2)
    await graph2.load()
    reloaded = graph2.get(s.id)
    assert reloaded.enforcement_mode == EnforcementMode.SHADOW
