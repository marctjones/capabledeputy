"""RPC handlers for policy inspection and simulated decisions (DESIGN.md §10.4)."""

from __future__ import annotations

from typing import Any

from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.engine import decide
from capabledeputy.policy.labels import LabelState


def make_policy_handlers() -> dict[str, Handler]:
    async def policy_show(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "capability_kinds": [kind.value for kind in CapabilityKind],
        }

    async def policy_test(params: dict[str, Any]) -> dict[str, Any]:
        capabilities = frozenset(Capability.from_dict(c) for c in params.get("capabilities", []))
        action = Action(
            kind=CapabilityKind(params["action_kind"]),
            target=params["target"],
            amount=params.get("amount"),
        )
        used_kinds = frozenset(CapabilityKind(k) for k in params.get("used_kinds", []))
        # Accept labels as either a dict (from LabelState.to_dict()) or reconstruct
        labels = None
        if params.get("labels"):
            labels_param = params["labels"]
            if isinstance(labels_param, dict):
                labels = LabelState.from_dict(labels_param)
        result = decide(capabilities, action, used_kinds=used_kinds, labels=labels)
        return {
            "decision": result.decision.value,
            "rule": result.rule,
            "reason": result.reason,
            "matched_capability": (
                result.matched_capability.to_dict() if result.matched_capability else None
            ),
        }

    async def policy_validate(params: dict[str, Any]) -> dict[str, Any]:
        return {"valid": True, "errors": []}

    return {
        "policy.show": policy_show,
        "policy.test": policy_test,
        "policy.validate": policy_validate,
    }
