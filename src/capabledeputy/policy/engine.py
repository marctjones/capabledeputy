"""Policy engine: capability-and-label-based decision API.

decide() is a pure function over (label_set, capability_set, action).
This makes the policy engine exhaustively testable and lets future
phases iterate on policy by replaying historical traces (DESIGN.md §9.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from capabledeputy.policy.actions import Action
from capabledeputy.policy.assurance import EffectGate, reversibility_gate
from capabledeputy.policy.bindings import BindingError, BindingSet
from capabledeputy.policy.capabilities import (
    DESTRUCTIVE_KINDS,
    Capability,
    CapabilityKind,
)
from capabledeputy.policy.decision_rules import (
    DecisionRules,
    EvaluationResult,
    RelaxInput,
    RelaxInspectionResult,
    RuleOutcome,
    inspect_relax_inputs,
)
from capabledeputy.policy.decision_rules import evaluate as _evaluate_v2
from capabledeputy.policy.labels import AxisA, AxisB, AxisD, Label
from capabledeputy.policy.optimistic import evaluate_optimistic
from capabledeputy.policy.overrides import OverrideGrantStore, use_override
from capabledeputy.policy.reversibility import ReversibilityLabel
from capabledeputy.policy.rules import CONFLICT_RULES, ConflictRule, Decision

_EGRESS_LABEL_FOR_KIND: dict[CapabilityKind, Label] = {
    CapabilityKind.SEND_EMAIL: Label.EGRESS_EMAIL,
    CapabilityKind.QUEUE_PURCHASE: Label.EGRESS_PURCHASE,
}

DESTRUCTIVE_OP_RULE = "destructive-op-needs-approval"
REVOKED_BY_PRIOR_USE_RULE = "capability-revoked-by-prior-use"
CAPABILITY_EXPIRED_RULE = "capability-expired"
RATE_LIMIT_EXCEEDED_RULE = "rate-limit-exceeded"
RELAX_REFUSED_RULE = "v2:relax_refused"
V2_RULE_PREFIX = "v2:"
BINDING_UNBOUND_RULE = "binding-unbound"
REVERSIBILITY_IRREVERSIBLE_RULE = "reversibility-irreversible"
REVERSIBILITY_REQUIRES_APPROVAL_RULE = "reversibility-requires-approval"
OPTIMISTIC_AUTO_RULE = "optimistic-auto"

# Effect-class substrings that mark an egressing action. Egress crosses
# the containment boundary; even reversible/system loses optimistic
# auto when egressing (see policy/optimistic.py docstring).
_EGRESS_EFFECT_MARKERS: tuple[str, ...] = (
    "egress",
    "send",
    "post",
    "write_remote",
    "share",
)


def _effect_class_is_egressing(effect_class: str | None) -> bool:
    if not effect_class:
        return False
    lo = effect_class.lower()
    return any(marker in lo for marker in _EGRESS_EFFECT_MARKERS)


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    rule: str | None = None
    reason: str | None = None
    matched_capability: Capability | None = None
    effective_labels: frozenset[Label] = frozenset()
    v2_outcome: RuleOutcome | None = None
    v2_matched_rule_ids: tuple[str, ...] = field(default_factory=tuple)
    # T046 — relax inputs that were refused per FR-031 asymmetry. When
    # non-empty, the decision is DENY and `rule == RELAX_REFUSED_RULE`.
    # The list is preserved on the decision so the caller can emit
    # `RELAXATION_REFUSED` audit events recording the offending inputs.
    refused_relax_inputs: tuple[RelaxInput, ...] = field(default_factory=tuple)
    # T048 — snapshots of the v2 axis inputs at decision time. When the
    # v2 leg ran, these are populated so the audit payload can record
    # the full Axis-A/B/D context + effect_class — enough for T041
    # decision-replay to reconstruct the same evaluate() outcome from
    # the logged event alone (FR-021).
    axis_a_snapshot: AxisA | None = None
    axis_b_snapshot: AxisB | None = None
    axis_d_snapshot: AxisD | None = None
    effect_class: str | None = None


_LEGACY_RANK: dict[Decision, int] = {
    Decision.DENY: 0,
    # OVERRIDE_REQUIRED sits between DENY and REQUIRE_APPROVAL: the
    # operator's override path can resolve it; ordinary approval
    # cannot. Composition ratchets stricter, so OVERRIDE_REQUIRED
    # wins over REQUIRE_APPROVAL and ALLOW.
    Decision.OVERRIDE_REQUIRED: 1,
    Decision.REQUIRE_APPROVAL: 2,
    Decision.ALLOW: 3,
}

# V2 RuleOutcome → legacy Decision mapping. SUGGEST collapses to
# REQUIRE_APPROVAL: the v2 evaluator says "surface this to a human"
# (FR-011 never-auto default), and the legacy chokepoint expresses
# that as REQUIRE_APPROVAL. AUTO maps to ALLOW only when reached by
# a human-ratified rule (evaluate() guarantees this — FR-014).
_V2_TO_LEGACY: dict[RuleOutcome, Decision] = {
    RuleOutcome.DENY: Decision.DENY,
    RuleOutcome.OVERRIDE_REQUIRED: Decision.OVERRIDE_REQUIRED,
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
    relax_inputs: tuple[RelaxInput, ...] = (),
    override_grants: OverrideGrantStore | None = None,
    session_id: Any = None,
    bindings: BindingSet | None = None,
    effective_reversibility: ReversibilityLabel | None = None,
) -> PolicyDecision:
    """Public chokepoint. Runs the legacy decision path, then — when
    the v2 axis inputs and rule set are provided — composes the v2
    decision-rule evaluator's result (FR-010/011/014/031). V2 may only
    ratchet stricter; legacy DENY always stands. When any v2 input is
    omitted, behavior is identical to the v0.7 engine (back-compat).

    FR-031 asymmetry (T046): if any element of `relax_inputs` has a
    non-deterministic origin (anything outside
    `decision_rules.ALLOWED_RELAX_ORIGINS`), the entire decision is
    refused — returned as DENY with rule `RELAX_REFUSED_RULE` — and
    the refused inputs are surfaced on `PolicyDecision.refused_relax_inputs`
    so the caller can emit a `RELAXATION_REFUSED` audit event.
    """
    # T079 override grant short-circuit. If an ACTIVE, not-expired,
    # not-consumed grant matches (session_id, action.kind, action.target),
    # mint the override-derived capability and short-circuit to ALLOW.
    # This is the only path that produces origin=OVERRIDE_GRANTED at
    # decide() time (FR-038).
    if override_grants is not None and session_id is not None and isinstance(session_id, UUID):
        eff_now = now if now is not None else datetime.now(UTC)
        active = override_grants.find_active(
            session_id=session_id,
            action_kind=action.kind,
            target=action.target,
            now=eff_now,
        )
        if active is not None:
            mint_result = use_override(
                active,
                action_kind=action.kind,
                target=action.target,
                now=eff_now,
            )
            if isinstance(mint_result, Capability):
                return PolicyDecision(
                    decision=Decision.ALLOW,
                    rule="override-grant-active",
                    reason=(
                        f"override grant {active.id} authorizes "
                        f"{action.kind.value}({action.target}) until "
                        f"{active.expires_at.isoformat()}"
                    ),
                    matched_capability=mint_result,
                    effective_labels=label_set,
                )

    # T077 / Demo #6 — binding canonicalization. When the operator
    # has wired a BindingSet, every write/egress destination must
    # canonicalize through it. An unbound or non-canonicalizable
    # target ⇒ refuse (FR-023 / FR-048 / SC-022). On success, the
    # canonical destination id replaces action.target for the v2
    # rule predicate so model-controlled case-varying inputs cannot
    # bypass rules authored against the canonical form.
    canonical_target: str | None = None
    if bindings is not None and action.target:
        try:
            resolution = bindings.resolve(action.target)
            canonical_target = resolution.canonical_destination_id
        except BindingError as e:
            return PolicyDecision(
                decision=Decision.DENY,
                rule=BINDING_UNBOUND_RULE,
                reason=str(e),
                effective_labels=label_set,
            )

    # T094 / T081 / Demo #4 — reversibility-weighted gating +
    # optimistic execution. When the caller supplies an effective
    # reversibility label AND an effect class, run the FR-019 gate.
    # social.* effects are hard-coded irreversible inside
    # reversibility_gate; the gate also routes reversible/system
    # toward optimistic auto (the result composes with everything
    # else most-restrictive — overrides and existing legacy DENY
    # always win).
    reversibility_outcome: Decision | None = None
    reversibility_rule: str | None = None
    reversibility_reason: str | None = None
    if effective_reversibility is not None and effect_class is not None:
        gate, _label, gate_rationale = reversibility_gate(
            effect_class=effect_class,
            declared_reversibility=effective_reversibility,
        )
        if gate is EffectGate.DENY:
            reversibility_outcome = Decision.DENY
            reversibility_rule = REVERSIBILITY_IRREVERSIBLE_RULE
            reversibility_reason = gate_rationale
        elif gate is EffectGate.REQUIRE_APPROVAL:
            reversibility_outcome = Decision.REQUIRE_APPROVAL
            reversibility_rule = REVERSIBILITY_REQUIRES_APPROVAL_RULE
            reversibility_reason = gate_rationale
        elif gate is EffectGate.AUTO_OK:
            # AUTO_OK means the gate allows; check optimistic auto.
            opt = evaluate_optimistic(
                effective_reversibility=effective_reversibility,
                is_egressing=_effect_class_is_egressing(effect_class),
            )
            if opt.should_auto:
                reversibility_outcome = Decision.ALLOW
                reversibility_rule = OPTIMISTIC_AUTO_RULE
                reversibility_reason = opt.rationale

    if relax_inputs:
        inspected: RelaxInspectionResult = inspect_relax_inputs(relax_inputs)
        if inspected.has_refusal:
            origins = sorted({r.origin for r in inspected.refused})
            return PolicyDecision(
                decision=Decision.DENY,
                rule=RELAX_REFUSED_RULE,
                reason=(
                    f"FR-031: relax input(s) from non-deterministic origin(s) {origins} refused"
                ),
                effective_labels=label_set,
                refused_relax_inputs=inspected.refused,
            )

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
        # Even on the legacy-only path, the reversibility gate
        # applies when the caller supplied an effective label.
        if reversibility_outcome is not None:
            return _compose_with_reversibility(
                legacy,
                reversibility_outcome=reversibility_outcome,
                reversibility_rule=reversibility_rule,
                reversibility_reason=reversibility_reason,
            )
        return legacy
    v2 = _evaluate_v2(
        rules=rules_v2,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class=effect_class,
        target=canonical_target if canonical_target is not None else action.target,
        default_when_no_match=default_v2_outcome,
    )
    composed = _compose_with_v2(legacy, v2)
    if reversibility_outcome is not None:
        composed = _compose_with_reversibility(
            composed,
            reversibility_outcome=reversibility_outcome,
            reversibility_rule=reversibility_rule,
            reversibility_reason=reversibility_reason,
        )
    return replace(
        composed,
        axis_a_snapshot=axis_a,
        axis_b_snapshot=axis_b,
        axis_d_snapshot=axis_d,
        effect_class=effect_class,
    )


def _compose_with_reversibility(
    base: PolicyDecision,
    *,
    reversibility_outcome: Decision,
    reversibility_rule: str | None,
    reversibility_reason: str | None,
) -> PolicyDecision:
    """Compose the reversibility-gate / optimistic-auto outcome with
    the legacy + v2 result. Most-restrictive wins UNLESS the
    reversibility outcome is OPTIMISTIC_AUTO and the base is
    REQUIRE_APPROVAL on the default v2 branch — in that case the
    optimistic auto carve-out applies (FR-034 reversible/system +
    non-egressing ⇒ auto without prompt).

    Concretely: a reversibility ALLOW (optimistic auto) only relaxes
    a base REQUIRE_APPROVAL when the base is the v2 default (no
    matching human-ratified rule); any DENY or rule-driven outcome
    still wins. This keeps Principle V (single chokepoint, no
    accidental relaxation) honest.
    """
    # Optimistic-auto carve-out: only relaxes a base REQUIRE_APPROVAL
    # produced by the v2 never-auto default, never relaxes DENY.
    if (
        reversibility_outcome is Decision.ALLOW
        and base.decision is Decision.REQUIRE_APPROVAL
        and base.v2_outcome is RuleOutcome.SUGGEST
    ):
        return replace(
            base,
            decision=Decision.ALLOW,
            rule=reversibility_rule,
            reason=reversibility_reason,
        )
    # Otherwise most-restrictive: if reversibility is stricter, take it.
    if _LEGACY_RANK[reversibility_outcome] < _LEGACY_RANK[base.decision]:
        return replace(
            base,
            decision=reversibility_outcome,
            rule=reversibility_rule,
            reason=reversibility_reason,
        )
    return base
