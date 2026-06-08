"""Slice D — grouped override (one confirmation authorizes a batch).

FR-035 grouping applied to Override (not just approval): a single friction
confirmation mints ONE grant covering a SET of (action_kind, target) members.
Each member is single-use; the grant stays ACTIVE until every member is used,
then CONSUMED. Avoids N override prompts for one logical "yes" — while keeping
the safety properties intact:

  - a target NOT in the member set is never authorized (redirection-resistance
    across the whole batch — untrusted content still can't add a destination);
  - the same policy gate as a single request (profile / authorized / friction);
  - each member exactly once (no replay).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import decide
from capabledeputy.policy.overrides import (
    GrantState,
    HardFloor,
    OverrideError,
    OverrideGrant,
    OverrideGrantStore,
    OverridePolicies,
    OverrideRefusal,
    OverrideRefusalReason,
    TrustProfile,
    request_group_override,
)
from capabledeputy.policy.rules import Decision

K = CapabilityKind
_MEMBERS = frozenset(
    {
        (K.SEND_EMAIL, "a@example.com"),
        (K.SEND_EMAIL, "b@example.com"),
        (K.SEND_EMAIL, "c@example.com"),
    },
)


def _personal() -> OverridePolicies:
    return OverridePolicies(
        by_floor={},
        trust_profile=TrustProfile.PERSONAL,
        operator_principal="owner",
    )


def _mint_group(policies: OverridePolicies, *, session_id, members=_MEMBERS, **kw):
    return request_group_override(
        policies=policies,
        session_id=session_id,
        members=members,
        target_category_tier=("personal", "restricted"),
        floor=HardFloor.PROVENANCE_EGRESS,
        invoker=kw.get("invoker", "owner"),
        friction_confirmed=kw.get("friction_confirmed", True),
    )


def _send(store, sid, target):
    return decide(
        frozenset(),  # no capability — only the grant can authorize
        Action(kind=K.SEND_EMAIL, target=target),
        override_grants=store,
        session_id=sid,
    )


# --- one confirmation authorizes the whole batch --------------------


def test_group_grant_authorizes_every_member_once() -> None:
    sid = uuid4()
    grant = _mint_group(_personal(), session_id=sid)
    assert isinstance(grant, OverrideGrant)
    assert grant.is_group
    store = OverrideGrantStore()
    store.add(grant)

    # Each of the three members goes through on the single confirmation.
    for target in ("a@example.com", "b@example.com", "c@example.com"):
        out = _send(store, sid, target)
        assert out.decision == Decision.ALLOW, target
        assert out.rule == "override-grant-active"

    # The grant is now fully consumed (single-use as a set).
    final = store.get(grant.id)
    assert final is not None
    assert final.state is GrantState.CONSUMED
    assert final.consumed_members == _MEMBERS


def test_group_member_is_single_use_but_siblings_unaffected() -> None:
    """Using member `a` consumes ONLY `a`; `b` still works, and a replay of
    `a` is refused."""
    sid = uuid4()
    grant = _mint_group(_personal(), session_id=sid)
    assert isinstance(grant, OverrideGrant)
    store = OverrideGrantStore()
    store.add(grant)

    assert _send(store, sid, "a@example.com").decision == Decision.ALLOW
    # Replay of a — already consumed, falls through to DENY (no cap).
    assert _send(store, sid, "a@example.com").decision == Decision.DENY
    # Sibling b — still authorized.
    assert _send(store, sid, "b@example.com").decision == Decision.ALLOW


def test_group_grant_does_not_authorize_non_member() -> None:
    """Redirection-resistance across the batch: a target outside the member
    set is never crossed — even after some members are used."""
    sid = uuid4()
    grant = _mint_group(_personal(), session_id=sid)
    assert isinstance(grant, OverrideGrant)
    store = OverrideGrantStore()
    store.add(grant)

    assert _send(store, sid, "attacker@evil.example").decision == Decision.DENY
    _send(store, sid, "a@example.com")  # use a real member
    assert _send(store, sid, "attacker@evil.example").decision == Decision.DENY


# --- same policy gate as a single request ---------------------------


def test_group_request_honors_friction() -> None:
    refusal = _mint_group(_personal(), session_id=uuid4(), friction_confirmed=False)
    assert isinstance(refusal, OverrideRefusal)
    assert refusal.reason == OverrideRefusalReason.FRICTION_NOT_MET


def test_group_request_managed_is_disallowed() -> None:
    managed = OverridePolicies(by_floor={}, trust_profile=TrustProfile.MANAGED)
    refusal = _mint_group(managed, session_id=uuid4())
    assert isinstance(refusal, OverrideRefusal)
    assert refusal.reason == OverrideRefusalReason.POLICY_DISALLOWED


def test_group_request_unauthorized_invoker_refused() -> None:
    refusal = _mint_group(_personal(), session_id=uuid4(), invoker="mallory")
    assert isinstance(refusal, OverrideRefusal)
    assert refusal.reason == OverrideRefusalReason.UNAUTHORIZED_INVOKER


def test_empty_group_is_a_caller_error() -> None:
    with pytest.raises(OverrideError, match="non-empty"):
        _mint_group(_personal(), session_id=uuid4(), members=frozenset())


# --- group grants are in-memory only (not persisted) ----------------


def test_group_grant_works_with_db_backed_store(tmp_path) -> None:
    """A db-backed store accepts a group grant without crashing (group
    grants are held in-memory; _persist is a no-op for them) and find_active
    still authorizes its members from memory."""
    sid = uuid4()
    store = OverrideGrantStore(db_path=tmp_path / "grants.db")
    grant = _mint_group(_personal(), session_id=sid)
    assert isinstance(grant, OverrideGrant)
    store.add(grant)
    assert _send(store, sid, "a@example.com").decision == Decision.ALLOW
