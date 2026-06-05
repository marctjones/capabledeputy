"""RPC handlers for the override workflow (003 US6 / FR-038).

CLI ↔ daemon bridge: the `capdep override` CLI dispatches through
these RPC handlers so grants land in the *daemon's* OverrideGrantStore
(persistent, consulted by engine.decide()), not a CLI-local in-memory
store. Without these handlers the CLI and daemon are isolated state
machines — a critical gap closed by this module.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.capabilities import CapabilityKind, kind_name
from capabledeputy.policy.overrides import (
    GrantState,
    HardFloor,
    OverrideGrant,
    OverrideGrantStore,
    OverridePolicies,
    OverrideRefusal,
    attest_override,
    request_override,
)


def _serialize_grant(grant: OverrideGrant) -> dict[str, Any]:
    return {
        "id": str(grant.id),
        "session_id": str(grant.session_id),
        "action_kind": kind_name(grant.action_kind),
        "target": grant.target,
        "target_category_tier": list(grant.target_category_tier),
        "hard_floor_crossed": grant.hard_floor_crossed.value,
        "invoker_principal": grant.invoker_principal,
        "attester_principal": grant.attester_principal,
        "policy_at_grant": {
            "floor": grant.policy_at_grant.floor.value,
            "policy": grant.policy_at_grant.policy.value,
            "expiry_seconds": grant.policy_at_grant.expiry_seconds,
        },
        "friction_level": grant.friction_level.value,
        "state": grant.state.value,
        "expires_at": grant.expires_at.isoformat(),
        "consumed_at": grant.consumed_at.isoformat() if grant.consumed_at else None,
        "audit_id": str(grant.audit_id),
    }


def _serialize_refusal(refusal: OverrideRefusal) -> dict[str, Any]:
    return {
        "refused": True,
        "reason": refusal.reason.value,
        "floor": refusal.floor.value if refusal.floor else None,
        "invoker": refusal.invoker,
        "detail": refusal.detail,
    }


def make_override_handlers(
    store: OverrideGrantStore,
    policies: OverridePolicies,
) -> dict[str, Handler]:
    """RPC handlers backed by the daemon's own store + policies."""

    async def override_request(params: dict[str, Any]) -> dict[str, Any]:
        result = request_override(
            policies=policies,
            session_id=UUID(params["session_id"]),
            action_kind=CapabilityKind(params["action_kind"]),
            target=str(params["target"]),
            target_category_tier=(
                str(params.get("category", "unknown")),
                str(params.get("tier", "restricted")),
            ),
            floor=HardFloor(params["floor"]),
            invoker=str(params["invoker"]),
            friction_confirmed=bool(params.get("friction_confirmed", False)),
        )
        if isinstance(result, OverrideRefusal):
            return _serialize_refusal(result)
        store.add(result)
        return _serialize_grant(result)

    async def override_attest(params: dict[str, Any]) -> dict[str, Any]:
        grant = store.get(UUID(params["grant_id"]))
        if grant is None:
            return {"refused": True, "reason": "unknown_grant"}
        result = attest_override(
            grant,
            attester=str(params["attester"]),
            confirmed=bool(params.get("confirmed", False)),
        )
        if isinstance(result, OverrideRefusal):
            return _serialize_refusal(result)
        store.update(result)
        return _serialize_grant(result)

    async def override_list(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "grants": [
                _serialize_grant(g) for g in sorted(store.list_all(), key=lambda g: g.expires_at)
            ],
        }

    async def override_show(params: dict[str, Any]) -> dict[str, Any]:
        grant = store.get(UUID(params["grant_id"]))
        if grant is None:
            return {"refused": True, "reason": "unknown_grant"}
        return _serialize_grant(grant)

    async def override_refuse(params: dict[str, Any]) -> dict[str, Any]:
        grant = store.get(UUID(params["grant_id"]))
        if grant is None:
            return {"refused": True, "reason": "unknown_grant"}
        refused = replace(grant, state=GrantState.REFUSED)
        store.update(refused)
        return _serialize_grant(refused)

    # Reserved for the auto-expiry sweep — could be invoked by a
    # background timer. Today decide()'s use_override is the only
    # auto-expiry path; this RPC exposes it for operator-driven
    # cleanup.
    async def override_sweep(_params: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC)
        expired_ids: list[str] = []
        for g in store.list_all():
            if g.state == GrantState.ACTIVE and g.is_expired(now):
                store.update(replace(g, state=GrantState.EXPIRED))
                expired_ids.append(str(g.id))
        return {"expired": expired_ids}

    return {
        "override.request": override_request,
        "override.attest": override_attest,
        "override.list": override_list,
        "override.show": override_show,
        "override.refuse": override_refuse,
        "override.sweep": override_sweep,
    }
