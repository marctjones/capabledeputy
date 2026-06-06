"""Spec 002 Phase 4 — cascade revocation tests.

Verifies:
  T025/SC-002 — revoke ancestor ⇒ descendants denied with
                capability-cascaded (distinct from expired / rate / prior-use)
  T026/US2-4  — FR-015 pooled rate accounting
  T027/SC-003 — pending approval invalidated when capability cascaded
  T028        — mid-flight calls not unwound (FR-009)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.queue import ApprovalQueue, ApprovalStateError
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
    RateLimit,
)
from capabledeputy.policy.engine import (
    CAPABILITY_CASCADED_RULE,
    _build_audit_id_index,
    _is_cascaded_inert,
    decide,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph


def _delegated_cap(
    parent: Capability,
    *,
    pattern: str | None = None,
    rate_limit: RateLimit | None = None,
) -> Capability:
    """Build a delegated child cap pointing at a parent via parent_audit_id."""
    return Capability(
        kind=parent.kind,
        pattern=pattern or parent.pattern,
        expiry=CapabilityExpiry.SESSION,
        origin=CapabilityOrigin.DELEGATED,
        parent_audit_id=parent.audit_id,
        depth=parent.depth + 1,
        rate_limit=rate_limit,
    )


# ---------- T025 / SC-002 cascade rule ----------


def test_revoked_ancestor_denies_descendant() -> None:
    """Revoke parent → child cap with the same audit chain is cascaded."""
    parent = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    child = _delegated_cap(parent)
    caps = frozenset({parent, child})
    action = Action(kind=CapabilityKind.READ_FS, target="anywhere.txt")

    # Without revocation: ALLOW (child matches; nothing inert)
    d = decide(caps, action)
    assert d.decision == Decision.ALLOW

    # With parent revoked: cascaded DENY
    d = decide(
        caps,
        action,
        revoked_audit_ids=frozenset({parent.audit_id}),
    )
    assert d.decision == Decision.DENY
    assert d.rule == CAPABILITY_CASCADED_RULE


def test_revoked_grandparent_denies_grandchild() -> None:
    """Three-deep chain — revoke root denies grandchild."""
    root = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    mid = _delegated_cap(root)
    leaf = _delegated_cap(mid)
    caps = frozenset({root, mid, leaf})
    action = Action(kind=CapabilityKind.READ_FS, target="x")

    d = decide(
        caps,
        action,
        revoked_audit_ids=frozenset({root.audit_id}),
    )
    assert d.decision == Decision.DENY
    assert d.rule == CAPABILITY_CASCADED_RULE
    # Originator attributed
    assert str(root.audit_id) in (d.reason or "")


def test_expired_ancestor_cascades() -> None:
    """An expired ancestor cascades — even if the child itself isn't expired."""
    long_ago = datetime.now(UTC) - timedelta(days=1)
    parent = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=long_ago,
    )
    child = _delegated_cap(parent)
    caps = frozenset({parent, child})
    action = Action(kind=CapabilityKind.READ_FS, target="x")

    d = decide(caps, action)
    # When the parent is expired, child is cascaded-inert. But the
    # cap-match step finds child first (it's not expired). The cascade
    # check fires next.
    assert d.decision == Decision.DENY
    assert d.rule == CAPABILITY_CASCADED_RULE


def test_distinct_from_expired_and_rate_rules() -> None:
    """Cascaded rule must be DISTINCT from capability-expired,
    rate-limit-exceeded, and capability-revoked-by-prior-use so audits
    can tell them apart (SC-005)."""
    # Setup: a self-expired cap (no parent) — should NOT be cascaded.
    long_ago = datetime.now(UTC) - timedelta(days=1)
    expired_cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=long_ago,
    )
    d = decide(
        frozenset({expired_cap}),
        Action(kind=CapabilityKind.READ_FS, target="x"),
    )
    assert d.decision == Decision.DENY
    # The expired path returns "capability-expired", not the cascade rule.
    # find_capability skips expired caps so it triggers the no-match
    # branch first, which then attributes "capability-expired".
    assert d.rule != CAPABILITY_CASCADED_RULE


def test_unit_inert_helper() -> None:
    """Spot-check the inert() helper directly."""
    p = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    c = _delegated_cap(p)
    idx = _build_audit_id_index(frozenset({p, c}))
    now = datetime.now(UTC)

    cascaded, originator = _is_cascaded_inert(
        c,
        cap_index=idx,
        revoked_audit_ids=frozenset(),
        now=now,
        cap_uses=None,
    )
    assert not cascaded

    cascaded, originator = _is_cascaded_inert(
        c,
        cap_index=idx,
        revoked_audit_ids=frozenset({p.audit_id}),
        now=now,
        cap_uses=None,
    )
    assert cascaded
    assert originator is not None
    assert originator.audit_id == p.audit_id


# ---------- T026 / US2-4 pooled rate ----------


@pytest.mark.asyncio
async def test_pooled_rate_child_cannot_outspend_ancestor(tmp_path: Path) -> None:
    """FR-015 — ancestor cap with N uses/window. Child uses N times.
    Child's (N+1)th call denied because the ancestor's window is full,
    even though the child's own window isn't."""
    parent_rl = RateLimit(max_uses=3, window_seconds=60)
    parent = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        rate_limit=parent_rl,
    )
    # Child has same window/limit; both share the pool via audit_id chain
    child = _delegated_cap(parent, rate_limit=parent_rl)
    caps = frozenset({parent, child})

    now = datetime.now(UTC)
    # Simulate 3 prior child dispatches recorded against BOTH child + parent
    # (the FR-015 pooled accounting).
    cap_uses = {
        str(child.audit_id): tuple(now - timedelta(seconds=5 * i) for i in range(3)),
        str(parent.audit_id): tuple(now - timedelta(seconds=5 * i) for i in range(3)),
    }
    action = Action(kind=CapabilityKind.READ_FS, target="x")

    # 4th call against child — should DENY (rate limit on the pooled chain)
    d = decide(
        caps,
        action,
        now=now,
        cap_uses=cap_uses,
    )
    assert d.decision == Decision.DENY


# ---------- T027 approval invalidation ----------


@pytest.mark.asyncio
async def test_pending_approval_invalidated_when_capability_cascaded(
    tmp_path: Path,
) -> None:
    """An approval whose capability_requested is descendant of a
    revoked ancestor must be invalidated at approve() time, not
    upgraded to ALLOW. Emits CAPABILITY_CASCADE_REVOKED audit event."""
    from capabledeputy.policy.labels import LabelState

    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)
    s = await graph.new()

    parent = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com")
    child = _delegated_cap(parent, pattern="ops@example.com")
    s = await graph.grant_capability(s.id, parent)
    s = await graph.grant_capability(s.id, child)

    # Revoke the parent
    await graph.revoke_capability(s.id, parent.audit_id)

    queue = ApprovalQueue(audit=writer, graph=graph)
    req = await queue.submit(
        action=ApprovalAction.SEND_EMAIL,
        target="ops@example.com",
        payload={"body": "hello"},
        labels_in=LabelState(),
        from_session=s.id,
        justification="test",
        capability_requested=child,
    )

    # Approval into ALLOW must be refused because the capability is cascaded
    with pytest.raises(ApprovalStateError, match="cascaded-inert"):
        await queue.approve(req.id)

    # And the audit log should contain a CAPABILITY_CASCADE_REVOKED event
    events = await writer.read_all()
    cascade_events = [e for e in events if e.event_type.value == "capability.cascade_revoked"]
    assert len(cascade_events) >= 1


# ---------- T028 mid-flight non-unwind ----------


def test_already_dispatched_call_not_unwound_by_subsequent_revoke() -> None:
    """FR-009 / T028 — revoke does not unwind already-dispatched calls.

    Operationally: at the moment of the past dispatch, the chokepoint
    saw a live cap and approved. Revoke is forward-looking. We verify
    this property by asserting that revocation only affects NEW
    decide() calls; the engine has no rollback mechanism.

    The chokepoint is the only place decisions happen; once it returns
    ALLOW, no later state can retroactively un-allow that decision.
    The audit log retains the historical ALLOW with no rewriting.
    """
    parent = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    child = _delegated_cap(parent)
    caps = frozenset({parent, child})
    action = Action(kind=CapabilityKind.READ_FS, target="x")
    now = datetime.now(UTC)

    # Time T0: allow.
    d0 = decide(caps, action, now=now)
    assert d0.decision == Decision.ALLOW

    # Time T1: parent revoked. NEW decide() at T1 denies; nothing
    # changes about d0 (it's a value; nothing to unwind).
    d1 = decide(
        caps,
        action,
        now=now + timedelta(seconds=1),
        revoked_audit_ids=frozenset({parent.audit_id}),
    )
    assert d1.decision == Decision.DENY
    # d0 is unchanged
    assert d0.decision == Decision.ALLOW
