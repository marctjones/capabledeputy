"""Policy engine: capability-and-label-based decision API.

decide() is a pure function over (label_set, capability_set, action).
This makes the policy engine exhaustively testable and lets future
phases iterate on policy by replaying historical traces (DESIGN.md §9.5).
"""

from __future__ import annotations

from dataclasses import dataclass

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    DESTRUCTIVE_KINDS,
    Capability,
    CapabilityKind,
)
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import CONFLICT_RULES, ConflictRule, Decision

_EGRESS_LABEL_FOR_KIND: dict[CapabilityKind, Label] = {
    CapabilityKind.SEND_EMAIL: Label.EGRESS_EMAIL,
    CapabilityKind.QUEUE_PURCHASE: Label.EGRESS_PURCHASE,
}

DESTRUCTIVE_OP_RULE = "destructive-op-needs-approval"


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    rule: str | None = None
    reason: str | None = None
    matched_capability: Capability | None = None
    effective_labels: frozenset[Label] = frozenset()


def egress_label_for(kind: CapabilityKind) -> Label | None:
    return _EGRESS_LABEL_FOR_KIND.get(kind)


def find_capability(
    capabilities: frozenset[Capability],
    action: Action,
) -> Capability | None:
    for cap in capabilities:
        if cap.matches(action.kind, action.target, action.amount):
            return cap
    return None


def decide(
    label_set: frozenset[Label],
    capabilities: frozenset[Capability],
    action: Action,
    rules: tuple[ConflictRule, ...] = CONFLICT_RULES,
) -> PolicyDecision:
    cap = find_capability(capabilities, action)
    if cap is None:
        return PolicyDecision(
            decision=Decision.DENY,
            reason=f"no matching capability for {action.kind.value}({action.target})",
            effective_labels=label_set,
        )

    effective_labels = label_set
    egress_label = egress_label_for(action.kind)
    if egress_label is not None:
        effective_labels = effective_labels | {egress_label}

    for rule in rules:
        if rule.fires(effective_labels):
            return PolicyDecision(
                decision=rule.decision,
                rule=rule.name,
                reason=(
                    f"rule {rule.name} fired on labels "
                    f"{sorted(label.value for label in effective_labels)}"
                ),
                matched_capability=cap,
                effective_labels=effective_labels,
            )

    # Destructive-op gate (DESIGN.md §7.5): MODIFY_* / DELETE_* actions
    # require a capability with `allows_destructive=True` OR an approval.
    # The default (`allows_destructive=False`) routes the action through
    # REQUIRE_APPROVAL so a human authorises it. This codifies the
    # Clark-Wilson well-formed-transaction principle: modifications are
    # deliberate, audited acts; never the implicit byproduct of a flow.
    if action.kind in DESTRUCTIVE_KINDS and not cap.allows_destructive:
        return PolicyDecision(
            decision=Decision.REQUIRE_APPROVAL,
            rule=DESTRUCTIVE_OP_RULE,
            reason=(
                f"{action.kind.value} on '{action.target}' is a destructive "
                "operation and the matched capability does not have "
                "allows_destructive=True"
            ),
            matched_capability=cap,
            effective_labels=effective_labels,
        )

    return PolicyDecision(
        decision=Decision.ALLOW,
        matched_capability=cap,
        effective_labels=effective_labels,
    )
