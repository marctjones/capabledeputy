"""Policy engine: capability-and-label-based decision API.

decide() is a pure function over (label_set, capability_set, action).
This makes the policy engine exhaustively testable and lets future
phases iterate on policy by replaying historical traces (DESIGN.md §9.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

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
REVOKED_BY_PRIOR_USE_RULE = "capability-revoked-by-prior-use"
CAPABILITY_EXPIRED_RULE = "capability-expired"


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
    now: datetime | None = None,
) -> Capability | None:
    """First scope/amount-matching capability. When `now` is provided,
    a matching-but-expired capability is skipped (treated as absent),
    so a non-expired sibling can still satisfy the action. Backward
    compatible: callers that pass no `now` get expiry-agnostic
    behavior (existing tests/paths unchanged)."""
    for cap in capabilities:
        if cap.matches(action.kind, action.target, action.amount):
            if now is not None and cap.is_expired(now):
                continue
            return cap
    return None


def decide(
    label_set: frozenset[Label],
    capabilities: frozenset[Capability],
    action: Action,
    rules: tuple[ConflictRule, ...] = CONFLICT_RULES,
    used_kinds: frozenset[CapabilityKind] = frozenset(),
    now: datetime | None = None,
) -> PolicyDecision:
    # Single decision clock: resolve once so every time-sensitive
    # check in this decision agrees. Deterministic and injectable —
    # never read inline, never influenced by the LLM (Principle I).
    eff_now = now if now is not None else datetime.now(UTC)

    cap = find_capability(capabilities, action, now=eff_now)
    if cap is None:
        # Distinguish "the only matching capabilities are expired"
        # from "never had a matching capability" so audits (and the
        # operator) can tell them apart (FR-003 / SC-005).
        expired_match = next(
            (
                c
                for c in capabilities
                if c.matches(action.kind, action.target, action.amount)
                and c.is_expired(eff_now)
            ),
            None,
        )
        if expired_match is not None:
            return PolicyDecision(
                decision=Decision.DENY,
                rule=CAPABILITY_EXPIRED_RULE,
                reason=(
                    f"capability for {action.kind.value}({action.target}) "
                    f"expired at {expired_match.expires_at.isoformat()} "
                    f"(decision time {eff_now.isoformat()})"
                ),
                matched_capability=expired_match,
                effective_labels=label_set,
            )
        return PolicyDecision(
            decision=Decision.DENY,
            reason=f"no matching capability for {action.kind.value}({action.target})",
            effective_labels=label_set,
        )

    # Tool-identity revocation: if the matched capability declares
    # revoked_by={K1, K2, ...} and any of those kinds has already been
    # dispatched in this session, deny. This is the tool-identity
    # counterpart to the label-based conflict rules.
    revoking = cap.revoked_by & used_kinds
    if revoking:
        return PolicyDecision(
            decision=Decision.DENY,
            rule=REVOKED_BY_PRIOR_USE_RULE,
            reason=(
                f"capability for {action.kind.value} was revoked by prior use of "
                f"{sorted(k.value for k in revoking)}"
            ),
            matched_capability=cap,
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
