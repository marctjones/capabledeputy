"""Daemon-side override RPC handler contract.

Pins the JSON shapes the CLI sends + receives. Without these, a
silent breaking change to the handler signatures would only surface
during a live daemon run with the CLI — too late.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from capabledeputy.daemon.override_handlers import make_override_handlers
from capabledeputy.policy.overrides import (
    HardFloor,
    OverrideGrantStore,
    OverridePolicies,
    OverridePolicy,
    OverridePolicyEntry,
)


def _policies() -> OverridePolicies:
    return OverridePolicies(
        by_floor={
            HardFloor.MAX_TIER_CLEARANCE: OverridePolicyEntry(
                floor=HardFloor.MAX_TIER_CLEARANCE,
                policy=OverridePolicy.SINGLE_AUTHORIZED,
                authorized_principal_ids=frozenset({"alice"}),
                expiry_seconds=300,
            ),
            HardFloor.ADMISSIBILITY_EXCLUSION: OverridePolicyEntry(
                floor=HardFloor.ADMISSIBILITY_EXCLUSION,
                policy=OverridePolicy.DUAL_CONTROL,
                authorized_principal_ids=frozenset({"alice"}),
                attester_principal_ids=frozenset({"bob"}),
                expiry_seconds=120,
            ),
        },
    )


@pytest.fixture
def handlers() -> tuple[dict[str, Any], OverrideGrantStore]:
    store = OverrideGrantStore()
    h = make_override_handlers(store, _policies())
    return h, store


async def test_request_returns_grant_dict(handlers: Any) -> None:
    h, _store = handlers
    result = await h["override.request"](
        {
            "session_id": str(uuid4()),
            "action_kind": "SEND_EMAIL",
            "target": "alice@example.com",
            "floor": "max-tier-clearance",
            "invoker": "alice",
            "category": "personal",
            "tier": "restricted",
            "friction_confirmed": True,
        },
    )
    assert "id" in result
    assert result["state"] == "active"
    assert result["invoker_principal"] == "alice"
    assert result["action_kind"] == "SEND_EMAIL"


async def test_request_refusal_serializes(handlers: Any) -> None:
    h, _store = handlers
    result = await h["override.request"](
        {
            "session_id": str(uuid4()),
            "action_kind": "SEND_EMAIL",
            "target": "x",
            "floor": "max-tier-clearance",
            "invoker": "mallory",  # unauthorized
            "friction_confirmed": True,
        },
    )
    assert result["refused"] is True
    assert result["reason"] == "unauthorized_invoker"


async def test_attest_round_trips_state(handlers: Any) -> None:
    h, _store = handlers
    request = await h["override.request"](
        {
            "session_id": str(uuid4()),
            "action_kind": "SEND_EMAIL",
            "target": "x",
            "floor": "admissibility-exclusion",
            "invoker": "alice",
            "friction_confirmed": True,
        },
    )
    assert request["state"] == "pending_attestation"
    attested = await h["override.attest"](
        {
            "grant_id": request["id"],
            "attester": "bob",
            "confirmed": True,
        },
    )
    assert attested["state"] == "active"
    assert attested["attester_principal"] == "bob"


async def test_list_returns_persistent_grants(handlers: Any) -> None:
    h, _store = handlers
    await h["override.request"](
        {
            "session_id": str(uuid4()),
            "action_kind": "SEND_EMAIL",
            "target": "x",
            "floor": "max-tier-clearance",
            "invoker": "alice",
            "friction_confirmed": True,
        },
    )
    listing = await h["override.list"]({})
    assert len(listing["grants"]) == 1


async def test_show_unknown_returns_refused(handlers: Any) -> None:
    h, _store = handlers
    result = await h["override.show"]({"grant_id": str(uuid4())})
    assert result["refused"] is True


async def test_refuse_transitions_grant_state(handlers: Any) -> None:
    h, _store = handlers
    request = await h["override.request"](
        {
            "session_id": str(uuid4()),
            "action_kind": "SEND_EMAIL",
            "target": "x",
            "floor": "max-tier-clearance",
            "invoker": "alice",
            "friction_confirmed": True,
        },
    )
    refused = await h["override.refuse"]({"grant_id": request["id"]})
    assert refused["state"] == "refused"


async def test_handlers_share_store_with_engine_lookup(handlers: Any) -> None:
    """The critical fix: the daemon's store seen by handlers is the
    same store engine.decide() consults via OverrideGrantStore.find_active.
    A grant issued via the RPC handler IS visible to the engine."""
    h, store = handlers
    request = await h["override.request"](
        {
            "session_id": str(uuid4()),
            "action_kind": "SEND_EMAIL",
            "target": "alice@example.com",
            "floor": "max-tier-clearance",
            "invoker": "alice",
            "friction_confirmed": True,
        },
    )
    # The handler-resolved store is the daemon's store; engine
    # lookup against the same object returns the grant.
    assert store.get(__import__("uuid").UUID(request["id"])) is not None
