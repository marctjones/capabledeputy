"""Composition Sub-phase C — Override grant short-circuits decide() (Demo #2).

When an ACTIVE override grant matches the (session, action_kind,
target) tuple at the chokepoint, engine.decide() mints a capability
with origin=OVERRIDE_GRANTED and returns ALLOW. The grant remains
valid until its expires_at; it is bounded (no perpetual override).

Demo path:
  1. Operator runs `capdep override request` → grant moves to ACTIVE
     (single-authorized) or PENDING_ATTESTATION (dual-control).
  2. (dual-control) Distinct attester runs `capdep override attest`
     → grant moves to ACTIVE.
  3. User invokes the gated action via the normal dispatch path.
  4. engine.decide() finds the active grant, mints the override
     capability, returns ALLOW.

These tests pin steps 3-4. The CLI surface (steps 1-2) is exercised
in tests/test_override_cli.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.engine import decide
from capabledeputy.policy.overrides import (
    FrictionLevel,
    GrantState,
    HardFloor,
    OverrideGrant,
    OverrideGrantStore,
    OverridePolicy,
    OverridePolicyEntry,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient, PolicyContext
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


def _active_grant(
    *,
    session_id: Any,
    action_kind: CapabilityKind = CapabilityKind.SEND_EMAIL,
    target: str = "alice@example.com",
    expires_at: datetime | None = None,
) -> OverrideGrant:
    return OverrideGrant(
        id=uuid4(),
        session_id=session_id,
        action_kind=action_kind,
        target=target,
        target_category_tier=("personal", "restricted"),
        hard_floor_crossed=HardFloor.MAX_TIER_CLEARANCE,
        invoker_principal="alice",
        attester_principal=None,
        policy_at_grant=OverridePolicyEntry(
            floor=HardFloor.MAX_TIER_CLEARANCE,
            policy=OverridePolicy.SINGLE_AUTHORIZED,
            authorized_principal_ids=frozenset({"alice"}),
        ),
        friction_level=FrictionLevel.MEDIUM,
        state=GrantState.ACTIVE,
        expires_at=expires_at or (datetime.now(UTC) + timedelta(seconds=300)),
    )


def test_active_grant_short_circuits_to_allow() -> None:
    """No legacy capability needed — the grant itself authorizes the
    action. decide() returns ALLOW with origin=OVERRIDE_GRANTED on
    the matched capability."""
    sid = uuid4()
    store = OverrideGrantStore()
    grant = _active_grant(session_id=sid)
    store.add(grant)
    result = decide(
        frozenset(),  # no labels
        frozenset(),  # no capabilities — grant carries the authority
        Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com"),
        override_grants=store,
        session_id=sid,
    )
    assert result.decision == Decision.ALLOW
    assert result.rule == "override-grant-active"
    assert result.matched_capability is not None
    assert result.matched_capability.origin == CapabilityOrigin.OVERRIDE_GRANTED
    assert result.matched_capability.override_grant_id == grant.id


def test_expired_grant_does_not_short_circuit() -> None:
    """A grant whose expires_at has passed is ignored — decide() falls
    through to the legacy path (which here denies for no cap)."""
    sid = uuid4()
    store = OverrideGrantStore()
    store.add(
        _active_grant(
            session_id=sid,
            expires_at=datetime(2020, 1, 1, tzinfo=UTC),
        ),
    )
    result = decide(
        frozenset(),
        frozenset(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com"),
        override_grants=store,
        session_id=sid,
    )
    assert result.decision == Decision.DENY


def test_grant_for_different_session_does_not_short_circuit() -> None:
    """Grants are session-bound; a grant issued to session A doesn't
    help session B."""
    session_a = uuid4()
    session_b = uuid4()
    store = OverrideGrantStore()
    store.add(_active_grant(session_id=session_a))
    result = decide(
        frozenset(),
        frozenset(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com"),
        override_grants=store,
        session_id=session_b,
    )
    assert result.decision == Decision.DENY


def test_grant_for_different_action_does_not_short_circuit() -> None:
    """A grant binds to (action_kind, target). Different action ⇒
    not short-circuited (legacy path runs)."""
    sid = uuid4()
    store = OverrideGrantStore()
    store.add(_active_grant(session_id=sid, action_kind=CapabilityKind.SEND_EMAIL))
    result = decide(
        frozenset(),
        frozenset(),
        Action(kind=CapabilityKind.READ_FS, target="alice@example.com"),
        override_grants=store,
        session_id=sid,
    )
    assert result.decision == Decision.DENY


def test_no_session_id_passed_skips_grant_lookup() -> None:
    """Back-compat: if the caller doesn't pass session_id, grant
    lookup is skipped (existing callers that don't know about
    override grants stay on the legacy path)."""
    sid = uuid4()
    store = OverrideGrantStore()
    store.add(_active_grant(session_id=sid))
    result = decide(
        frozenset(),
        frozenset(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com"),
        override_grants=store,
        # session_id intentionally omitted
    )
    assert result.decision == Decision.DENY


# --- end-to-end via LabeledToolClient -------------------------------


async def _make_session(graph: SessionGraph) -> Any:
    return await graph.new()


@pytest.fixture
def writer(tmp_path: Any) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


async def _ok_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(output={"ok": True, "args": args})


def _send_email_tool() -> ToolDefinition:
    return ToolDefinition(
        name="email.send",
        description="t",
        capability_kind=CapabilityKind.SEND_EMAIL,
        handler=_ok_handler,
        target_arg="to",
        operations=(Operation(EffectClass.FETCH),),
        risk_ids=("RISK-INDIRECT-INJECTION",),
    )


async def test_grant_unlocks_dispatch_end_to_end(writer: AuditWriter) -> None:
    """Demo #2 path: a session with NO matching capability still gets
    ALLOW via the grant, mints an OVERRIDE_GRANTED capability, runs
    the handler."""
    registry = ToolRegistry()
    registry.register(_send_email_tool())
    graph = SessionGraph()
    s = await _make_session(graph)
    store = OverrideGrantStore()
    grant = OverrideGrant(
        id=uuid4(),
        session_id=s.id,
        action_kind=CapabilityKind.SEND_EMAIL,
        target="alice@example.com",
        target_category_tier=("personal", "restricted"),
        hard_floor_crossed=HardFloor.MAX_TIER_CLEARANCE,
        invoker_principal="alice",
        attester_principal=None,
        policy_at_grant=OverridePolicyEntry(
            floor=HardFloor.MAX_TIER_CLEARANCE,
            policy=OverridePolicy.SINGLE_AUTHORIZED,
            authorized_principal_ids=frozenset({"alice"}),
        ),
        friction_level=FrictionLevel.MEDIUM,
        state=GrantState.ACTIVE,
        expires_at=datetime.now(UTC) + timedelta(seconds=300),
    )
    store.add(grant)
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(override_grants=store),
    )
    outcome = await client.call_tool(
        s.id,
        "email.send",
        {"to": "alice@example.com"},
    )
    assert outcome.decision == Decision.ALLOW
    assert outcome.output == {"ok": True, "args": {"to": "alice@example.com"}}


async def test_no_grant_no_capability_denies(writer: AuditWriter) -> None:
    """Sanity: same setup without the grant ⇒ DENY (no matching cap)."""
    registry = ToolRegistry()
    registry.register(_send_email_tool())
    graph = SessionGraph()
    s = await _make_session(graph)
    store = OverrideGrantStore()  # empty
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(override_grants=store),
    )
    outcome = await client.call_tool(
        s.id,
        "email.send",
        {"to": "alice@example.com"},
    )
    assert outcome.decision == Decision.DENY


def test_grant_store_find_active_returns_none_when_no_match() -> None:
    store = OverrideGrantStore()
    result = store.find_active(
        session_id=uuid4(),
        action_kind=CapabilityKind.SEND_EMAIL,
        target="alice@example.com",
        now=datetime.now(UTC),
    )
    assert result is None
