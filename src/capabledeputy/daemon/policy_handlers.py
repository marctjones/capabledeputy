"""RPC handlers for policy inspection and simulated decisions (DESIGN.md §10.4)."""

from __future__ import annotations

from typing import Any

from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.engine import decide
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import CONFLICT_RULES


def make_policy_handlers() -> dict[str, Handler]:
    async def policy_show(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "labels": [label.value for label in Label],
            "capability_kinds": [kind.value for kind in CapabilityKind],
            "rules": [
                {
                    "name": rule.name,
                    "triggers": sorted(trigger.value for trigger in rule.triggers),
                    "conflicts": sorted(conflict.value for conflict in rule.conflicts),
                    "decision": rule.decision.value,
                }
                for rule in CONFLICT_RULES
            ],
        }

    async def policy_test(params: dict[str, Any]) -> dict[str, Any]:
        labels = frozenset(Label(s) for s in params.get("labels", []))
        capabilities = frozenset(Capability.from_dict(c) for c in params.get("capabilities", []))
        action = Action(
            kind=CapabilityKind(params["action_kind"]),
            target=params["target"],
            amount=params.get("amount"),
        )
        used_kinds = frozenset(
            CapabilityKind(k) for k in params.get("used_kinds", [])
        )
        result = decide(labels, capabilities, action, used_kinds=used_kinds)
        return {
            "decision": result.decision.value,
            "rule": result.rule,
            "reason": result.reason,
            "matched_capability": (
                result.matched_capability.to_dict() if result.matched_capability else None
            ),
            "effective_labels": sorted(label.value for label in result.effective_labels),
        }

    async def policy_validate(params: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        seen_names: set[str] = set()
        for rule in CONFLICT_RULES:
            if rule.name in seen_names:
                errors.append(f"duplicate rule name: {rule.name}")
            seen_names.add(rule.name)
            if not rule.triggers:
                errors.append(f"rule {rule.name} has empty triggers")
            if not rule.conflicts:
                errors.append(f"rule {rule.name} has empty conflicts")
        return {"valid": not errors, "errors": errors}

    return {
        "policy.show": policy_show,
        "policy.test": policy_test,
        "policy.validate": policy_validate,
    }
