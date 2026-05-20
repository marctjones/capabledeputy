"""Policy engine: capability-and-label-based decision API.

decide() is a pure function over (label_set, capability_set, action).
This makes the policy engine exhaustively testable and lets future
phases iterate on policy by replaying historical traces (DESIGN.md §9.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    DESTRUCTIVE_KINDS,
    Capability,
    CapabilityKind,
)
from capabledeputy.policy.decision_rules import (
    DecisionRules,
    EvaluationResult,
    RuleOutcome,
)
from capabledeputy.policy.decision_rules import evaluate as _evaluate_v2
from capabledeputy.policy.labels import AxisA, AxisB, AxisD, Label
from capabledeputy.policy.rules import CONFLICT_RULES, ConflictRule, Decision

_EGRESS_LABEL_FOR_KIND: dict[CapabilityKind, Label] = {
    CapabilityKind.SEND_EMAIL: Label.EGRESS_EMAIL,
    CapabilityKind.QUEUE_PURCHASE: Label.EGRESS_PURCHASE,
}

DESTRUCTIVE_OP_RULE = "destructive-op-needs-approval"
REVOKED_BY_PRIOR_USE_RULE = "capability-revoked-by-prior-use"
CAPABILITY_EXPIRED_RULE = "capability-expired"
RATE_LIMIT_EXCEEDED_RULE = "rate-limit-exceeded"
V2_RULE_PREFIX = "v2:"


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    rule: str | None = None
    reason: str | None = None
    matched_capability: Capability | None = None
    effective_labels: frozenset[Label] = frozenset()
    v2_outcome: RuleOutcome | None = None
    v2_matched_rule_ids: tuple[str, ...] = field(default_factory=tuple)


_LEGACY_RANK: dict[Decision, int] = {
    Decision.DENY: 0,
    Decision.REQUIRE_APPROVAL: 1,
    Decision.ALLOW: 2,
}

# V2 RuleOutcome → legacy Decision mapping. SUGGEST collapses to
# REQUIRE_APPROVAL: the v2 evaluator says "surface this to a human"
# (FR-011 never-auto default), and the legacy chokepoint expresses
# that as REQUIRE_APPROVAL. AUTO maps to ALLOW only when reached by
# a human-ratified rule (evaluate() guarantees this — FR-014).
_V2_TO_LEGACY: dict[RuleOutcome, Decision] = {
    RuleOutcome.DENY: Decision.DENY,
    RuleOutcome.REQUIRE_APPROVAL: Decision.REQUIRE_APPROVAL,
    RuleOutcome.SUGGEST: Decision.REQUIRE_APPROVAL,
    RuleOutcome.AUTO: Decision.ALLOW,
}


def _compose_with_v2(legacy: PolicyDecision, v2: EvaluationResult) -> PolicyDecision:
    """Compose the legacy PolicyDecision with the v2 EvaluationResult.

    FR-031 asymmetry: v2 may only ratchet stricter, never relax. If
    legacy already denies/requires-approval and v2 says AUTO, the
    legacy outcome stands — but v2_outcome/v2_matched_rule_ids are
    still recorded on the decision for audit (T048).
    """
    v2_as_legacy = _V2_TO_LEGACY[v2.outcome]
    if _LEGACY_RANK[v2_as_legacy] < _LEGACY_RANK[legacy.decision]:
        # v2 ratchets stricter.
        rule_label = (
            V2_RULE_PREFIX + ",".join(v2.matched_rule_ids)
            if v2.matched_rule_ids
            else V2_RULE_PREFIX + "default"
        )
        return replace(
            legacy,
            decision=v2_as_legacy,
            rule=rule_label,
            reason=v2.rationale,
            v2_outcome=v2.outcome,
            v2_matched_rule_ids=v2.matched_rule_ids,
        )
    return replace(
        legacy,
        v2_outcome=v2.outcome,
        v2_matched_rule_ids=v2.matched_rule_ids,
    )


def egress_label_for(kind: CapabilityKind) -> Label | None:
    return _EGRESS_LABEL_FOR_KIND.get(kind)


def _cap_uses_for(
    cap: Capability,
    cap_uses: dict[str, tuple[datetime, ...]] | None,
) -> tuple[datetime, ...]:
    if cap_uses is None:
        return ()
    return cap_uses.get(str(cap.audit_id), ())


def find_capability(
    capabilities: frozenset[Capability],
    action: Action,
    now: datetime | None = None,
    cap_uses: dict[str, tuple[datetime, ...]] | None = None,
) -> Capability | None:
    """First scope/amount-matching capability that is also usable. When
    `now` is provided a matching-but-expired capability is skipped; when
    `cap_uses` is provided a matching-but-rate-exceeded one is skipped —
    in both cases treated as absent so a still-usable sibling can
    satisfy the action. Backward compatible: callers that pass neither
    get the original expiry/rate-agnostic behavior."""
    for cap in capabilities:
        if cap.matches(action.kind, action.target, action.amount):
            if now is not None and cap.is_expired(now):
                continue
            if now is not None and cap.is_rate_exceeded(now, _cap_uses_for(cap, cap_uses)):
                continue
            return cap
    return None


def _decide_legacy(
    label_set: frozenset[Label],
    capabilities: frozenset[Capability],
    action: Action,
    rules: tuple[ConflictRule, ...] = CONFLICT_RULES,
    used_kinds: frozenset[CapabilityKind] = frozenset(),
    now: datetime | None = None,
    cap_uses: dict[str, tuple[datetime, ...]] | None = None,
) -> PolicyDecision:
    # Single decision clock: resolve once so every time-sensitive
    # check in this decision agrees. Deterministic and injectable —
    # never read inline, never influenced by the LLM (Principle I).
    eff_now = now if now is not None else datetime.now(UTC)

    cap = find_capability(
        capabilities,
        action,
        now=eff_now,
        cap_uses=cap_uses,
    )
    if cap is None:
        # Distinguish "the only matching capabilities are expired"
        # from "never had a matching capability" so audits (and the
        # operator) can tell them apart (FR-003 / SC-005).
        expired_match = next(
            (
                c
                for c in capabilities
                if c.matches(action.kind, action.target, action.amount) and c.is_expired(eff_now)
            ),
            None,
        )
        if expired_match is not None:
            # is_expired() is True only when expires_at is set, so the
            # deadline is non-None here; assert the invariant so the
            # type is narrowed (and a future is_expired change can't
            # silently produce a None deref).
            deadline = expired_match.expires_at
            assert deadline is not None
            return PolicyDecision(
                decision=Decision.DENY,
                rule=CAPABILITY_EXPIRED_RULE,
                reason=(
                    f"capability for {action.kind.value}({action.target}) "
                    f"expired at {deadline.isoformat()} "
                    f"(decision time {eff_now.isoformat()})"
                ),
                matched_capability=expired_match,
                effective_labels=label_set,
            )
        # Next: a scope-matching capability that is only disqualified
        # because its sliding-window rate limit is exhausted. Distinct
        # rule so audits separate "too many uses" from "expired" and
        # "never had it".
        rate_match = next(
            (
                c
                for c in capabilities
                if c.matches(action.kind, action.target, action.amount)
                and c.is_rate_exceeded(
                    eff_now,
                    _cap_uses_for(c, cap_uses),
                )
            ),
            None,
        )
        if rate_match is not None and rate_match.rate_limit is not None:
            rl = rate_match.rate_limit
            return PolicyDecision(
                decision=Decision.DENY,
                rule=RATE_LIMIT_EXCEEDED_RULE,
                reason=(
                    f"capability for {action.kind.value}({action.target}) "
                    f"rate limit exceeded: {rl.max_uses} uses per "
                    f"{rl.window_seconds}s (decision time "
                    f"{eff_now.isoformat()})"
                ),
                matched_capability=rate_match,
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


def decide(
    label_set: frozenset[Label],
    capabilities: frozenset[Capability],
    action: Action,
    rules: tuple[ConflictRule, ...] = CONFLICT_RULES,
    used_kinds: frozenset[CapabilityKind] = frozenset(),
    now: datetime | None = None,
    cap_uses: dict[str, tuple[datetime, ...]] | None = None,
    *,
    axis_a: AxisA | None = None,
    axis_b: AxisB | None = None,
    axis_d: AxisD | None = None,
    effect_class: str | None = None,
    rules_v2: DecisionRules | None = None,
    default_v2_outcome: RuleOutcome = RuleOutcome.SUGGEST,
) -> PolicyDecision:
    """Public chokepoint. Runs the legacy decision path, then — when
    the v2 axis inputs and rule set are provided — composes the v2
    decision-rule evaluator's result (FR-010/011/014/031). V2 may only
    ratchet stricter; legacy DENY always stands. When any v2 input is
    omitted, behavior is identical to the v0.7 engine (back-compat)."""
    legacy = _decide_legacy(
        label_set,
        capabilities,
        action,
        rules,
        used_kinds,
        now,
        cap_uses,
    )
    if (
        axis_a is None
        or axis_b is None
        or axis_d is None
        or effect_class is None
        or rules_v2 is None
    ):
        return legacy
    v2 = _evaluate_v2(
        rules=rules_v2,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class=effect_class,
        target=action.target,
        default_when_no_match=default_v2_outcome,
    )
    return _compose_with_v2(legacy, v2)
