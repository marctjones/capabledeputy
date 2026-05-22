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
from capabledeputy.policy.envelope import (
    CellKey,
    EnvelopeSet,
    RiskPreference,
)
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
ENVELOPE_DIAL_RULE = "envelope-dial"
CONTROL_PLANE_TAINTED_RULE = "control-plane-tainted-session"
CLEARANCE_REFUSED_RULE = "clearance-refused"
INTEGRITY_FLOOR_REFUSED_RULE = "integrity-floor-refused"
SANDBOX_NO_ACTUATOR_RULE = "sandbox-no-actuator"
ORPHAN_RISK_CITATION_RULE = "orphan-risk-citation"

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
class RecoveryStep:
    """A literal slash command an operator can paste to make progress
    on a denied action. Issue #3.

    `command` is the slash command name (e.g. "/grant", "/spawn",
    "/override", "/extract"). `args` are the positional + flag tokens
    that follow. `rationale` is a one-line explanation for the
    operator — never blamed on the agent, always actionable.

    The synthesizer (Issue #3) maps every non-ALLOW decision rule to
    a determined sequence. Operators paste; agents quote literally.
    """

    command: str
    args: tuple[str, ...]
    rationale: str

    def as_command_line(self) -> str:
        return f"{self.command} {' '.join(self.args)}".strip()


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
    # Issue #3 — Recovery synthesis. Empty tuple when the decision is
    # ALLOW or when no slash-command recovery exists for the rule
    # (e.g. EXECUTE.sandbox-no-actuator requires operator action,
    # not a chat command). Populated for every other non-ALLOW path.
    # Renders in the REPL as literal pasteable commands; exposed via
    # policy.preview so the agent can quote without inventing.
    recovery_steps: tuple[RecoveryStep, ...] = field(default_factory=tuple)


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


CAPABILITY_CASCADED_RULE = "capability-cascaded"


def _build_audit_id_index(
    capabilities: frozenset[Capability],
) -> dict[UUID, Capability]:
    """Build a map from audit_id → Capability for O(1) ancestor lookups
    during cascade inert checks. Pure function of the capability set."""
    return {c.audit_id: c for c in capabilities}


def _is_cascaded_inert(
    cap: Capability,
    *,
    cap_index: dict[UUID, Capability],
    revoked_audit_ids: frozenset[UUID],
    now: datetime,
    cap_uses: dict[str, tuple[datetime, ...]] | None,
) -> tuple[bool, Capability | None]:
    """Walk `cap`'s ancestor chain via parent_audit_id. Returns
    (True, originating_ancestor) if any link is inert — self or any
    ancestor revoked/expired/rate-exhausted. Returns (False, None) if
    every link is live.

    inert(C) = C.audit_id in revoked_audit_ids OR C.is_expired OR
               C.is_rate_exceeded OR (C has a parent AND inert(parent))

    Computed at decision time per research D1 — no eager mutation.
    """
    visited: set[UUID] = set()
    current: Capability | None = cap
    while current is not None:
        # Cycle defense (parent_audit_id is single-parent by design,
        # but a corrupt store could create one; treat cycle as inert).
        if current.audit_id in visited:
            return True, current
        visited.add(current.audit_id)

        if current.audit_id in revoked_audit_ids:
            return True, current
        if current.is_expired(now):
            return True, current
        if current.is_rate_exceeded(now, _cap_uses_for(current, cap_uses)):
            return True, current

        if current.parent_audit_id is None:
            return False, None
        current = cap_index.get(current.parent_audit_id)
        if current is None:
            # Parent referenced but absent from this session's cap set —
            # treat as inert (the chain is broken; fail-closed).
            return True, None
    return False, None


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
    revoked_audit_ids: frozenset[UUID] = frozenset(),
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

    # 002 US2 cascade revocation: if the matched capability OR any
    # ancestor (via parent_audit_id chain) is revoked / expired /
    # rate-exhausted, the descendant inherits inert status — DENY
    # with capability-cascaded attributing the originating ancestor.
    # Computed at decision time per research D1 (no eager mutation).
    cap_index = _build_audit_id_index(capabilities)
    cascaded, originator = _is_cascaded_inert(
        cap,
        cap_index=cap_index,
        revoked_audit_ids=revoked_audit_ids,
        now=eff_now,
        cap_uses=cap_uses,
    )
    if cascaded:
        originator_id = originator.audit_id if originator is not None else cap.audit_id
        return PolicyDecision(
            decision=Decision.DENY,
            rule=CAPABILITY_CASCADED_RULE,
            reason=(
                f"capability for {action.kind.value}({action.target}) "
                f"is cascaded-inert: originating ancestor "
                f"audit_id={originator_id} (revoked/expired/exhausted)"
            ),
            matched_capability=cap,
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


def _synthesize_recovery_steps(
    *,
    decision: Decision,
    rule: str | None,
    action: Action,
    effect_class: str | None,
    reason: str | None = None,
) -> tuple[RecoveryStep, ...]:
    """Issue #3 — map a non-ALLOW decision to literal pasteable slash
    commands. Returns empty tuple when no slash-command recovery
    exists (e.g. EXECUTE.sandbox without a wired actuator — operator
    must edit daemon config).

    The output renders directly in the REPL and is exposed via
    `policy.preview` so the agent quotes verbatim instead of inventing
    commands.
    """
    if decision is Decision.ALLOW:
        return ()

    kind = action.kind.value if action.kind else "READ_FS"
    target = action.target or "*"

    # Reason-based fallback: the legacy "no matching capability" path
    # returns rule=None but a stable reason string. Detect via reason
    # so the synthesizer covers it without changing the engine's
    # rule contract (test_policy_engine.py pins rule is None here).
    if rule is None:
        if reason and "no matching capability" in reason.lower():
            return (
                RecoveryStep(
                    command="/grant",
                    args=(kind, target, "--one-shot"),
                    rationale=f"Session lacks a capability for {kind} on {target}.",
                ),
            )
        return ()

    # No-matching-capability — the simplest case. Just grant the cap.
    if rule == "no-matching-capability" or "no-matching" in rule:
        return (
            RecoveryStep(
                command="/grant",
                args=(kind, target, "--one-shot"),
                rationale=f"Session lacks a capability for {kind} on {target}.",
            ),
        )

    # Capability expired — re-grant with a fresh TTL.
    if rule == "capability-expired" or "expired" in rule:
        return (
            RecoveryStep(
                command="/grant",
                args=(kind, target, "--one-shot", "--ttl", "3600"),
                rationale="Previous capability's deadline passed; grant a fresh one.",
            ),
        )

    # Destructive op without the destructive flag.
    if rule == DESTRUCTIVE_OP_RULE or "destructive" in rule.lower():
        return (
            RecoveryStep(
                command="/grant",
                args=(kind, target, "--one-shot", "--destructive"),
                rationale="Action is destructive; grant must explicitly authorize it.",
            ),
        )

    # Rate-limit exhausted — grant a higher-rate cap or wait.
    if rule == RATE_LIMIT_EXCEEDED_RULE or "rate-limit" in rule.lower():
        return (
            RecoveryStep(
                command="/grant",
                args=(kind, target, "--one-shot", "--rate", "10/hour"),
                rationale="Rate window exhausted; grant a higher-rate cap or wait.",
            ),
        )

    # Capability revoked by prior use — fresh session is the cleanest
    # path. The new session has no prior-use record.
    if rule == REVOKED_BY_PRIOR_USE_RULE:
        intent = f"continue with {kind}"
        return (
            RecoveryStep(
                command="/spawn",
                args=(f'"{intent}"',),
                rationale="Capability was revoked by a prior tool use; fresh session resets the record.",
            ),
            RecoveryStep(
                command="/grant",
                args=(kind, target, "--one-shot"),
                rationale="Grant the capability in the fresh session.",
            ),
        )

    # Sandbox effect-class without wired actuator — no slash recovery.
    if rule == SANDBOX_NO_ACTUATOR_RULE or (
        effect_class == "EXECUTE.sandbox" and "actuator" in rule.lower()
    ):
        return ()  # Operator must wire a SandboxActuator in daemon config.

    # Label-conflict family (untrusted/financial/health-meets-egress).
    # The session has accumulated labels that conflict with the action's
    # egress. Three recovery paths:
    #   1. /spawn a clean session (primary — simplest)
    #   2. /extract via quarantined declassification (when a schema exists)
    #   3. /override request (operator's escape hatch)
    if "meets-egress" in rule or "meets-email" in rule:
        intent_hint = f"send to {target}" if target and "@" in target else f"continue with {kind}"
        return (
            RecoveryStep(
                command="/spawn",
                args=(f'"{intent_hint}"',),
                rationale="Session is tainted by prior reads of untrusted/sensitive content; a fresh session has no labels to conflict.",
            ),
            RecoveryStep(
                command="/grant",
                args=(kind, target, "--one-shot"),
                rationale="Grant the capability in the fresh session.",
            ),
            RecoveryStep(
                command="/override",
                args=("request", kind, target, "--justification", f'"explicit user authorization for {kind} on {target}"'),
                rationale="Alternative: request operator override to bypass the label conflict in this session.",
            ),
        )

    # v2 relax-refused — operator's policy floor blocks the requested
    # relaxation. Same shape as label conflicts — clean session or
    # override path.
    if rule == RELAX_REFUSED_RULE:
        return (
            RecoveryStep(
                command="/override",
                args=("request", kind, target, "--justification", '"operator-floor relaxation needed"'),
                rationale="A v2 rule refused to relax to ALLOW; operator override is the only path.",
            ),
        )

    # Unknown rule — synthesizer doesn't know how to recover.
    return ()


def _decide_impl(
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
    envelope_set: EnvelopeSet | None = None,
    risk_preference: RiskPreference | None = None,
    clearance_max_tier: Any = None,
    integrity_floor_level: str | None = None,
    risk_register: Any = None,
    sandbox_actuator_wired: bool = False,
    revoked_audit_ids: frozenset[UUID] = frozenset(),
) -> PolicyDecision:
    """Internal decision impl. The public `decide()` wraps this and
    adds recovery-step synthesis (Issue #3) on the resulting
    non-ALLOW outcomes.

    Runs the legacy decision path, then — when the v2 axis inputs and
    rule set are provided — composes the v2 decision-rule evaluator's
    result (FR-010/011/014/031). V2 may only ratchet stricter; legacy
    DENY always stands. When any v2 input is omitted, behavior is
    identical to the v0.7 engine (back-compat).

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
                # FR-036 single-use: mark the grant CONSUMED so a
                # subsequent decide() falls back to the normal policy.
                from dataclasses import replace as _dc_replace

                from capabledeputy.policy.overrides import GrantState

                consumed = _dc_replace(
                    active,
                    state=GrantState.CONSUMED,
                    consumed_at=eff_now,
                )
                override_grants.update(consumed)
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

    # Sub-phase G / Demo #7 — control-plane reflexivity (FR-018).
    # A tainted session (AxisB carries external-untrusted) cannot
    # exercise any ADMINISTER-class effect. Refused before legacy/v2
    # so the audit reason is unambiguous.
    if effect_class is not None and axis_b is not None:
        from capabledeputy.policy.assurance import (
            control_plane_admissible,
            is_control_plane_effect,
        )

        if is_control_plane_effect(effect_class) and not control_plane_admissible(
            effect_class=effect_class,
            axis_b=axis_b,
        ):
            return PolicyDecision(
                decision=Decision.DENY,
                rule=CONTROL_PLANE_TAINTED_RULE,
                reason=(
                    f"FR-018: session with external-untrusted provenance "
                    f"cannot exercise control-plane effect {effect_class!r}"
                ),
                effective_labels=label_set,
                axis_a_snapshot=axis_a,
                axis_b_snapshot=axis_b,
                axis_d_snapshot=axis_d,
                effect_class=effect_class,
            )

    # Sandbox-without-actuator (FR-042 / SC-017) — EXECUTE.sandbox
    # invoked when no SandboxActuator port is wired ⇒ refuse with the
    # explicit rule so the operator can wire a provider or use Pattern
    # (3) instead.
    if (
        effect_class is not None
        and effect_class.lower().startswith("execute.sandbox")
        and not sandbox_actuator_wired
    ):
        return PolicyDecision(
            decision=Decision.OVERRIDE_REQUIRED,
            rule=SANDBOX_NO_ACTUATOR_RULE,
            reason=(
                f"FR-042/SC-017: {effect_class!r} requires a SandboxActuator "
                f"port; none wired (spec 004 provider impl)"
            ),
            effective_labels=label_set,
            axis_a_snapshot=axis_a,
            axis_b_snapshot=axis_b,
            axis_d_snapshot=axis_d,
            effect_class=effect_class,
        )

    # Orphan risk-citation refusal (FR-015 runtime side). If a risk
    # register is wired AND axis_a labels declare risk_ids, any id NOT
    # in the register refuses the decision. The CI lint already catches
    # missing framework_refs at build time; this catches runtime
    # citations of unknown ids.
    if risk_register is not None and axis_a is not None:
        from capabledeputy.policy.assurance import validate_label_citation

        for cat in axis_a.categories:
            # Categories that don't declare any risk_ids are a CI-lint
            # concern (SC-001); runtime check focuses on category that
            # DOES cite ids, and refuses when any id is unknown.
            if not cat.risk_ids:
                continue
            orphans = validate_label_citation(
                risk_ids=cat.risk_ids,
                register=risk_register,
            )
            if orphans:
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule=ORPHAN_RISK_CITATION_RULE,
                    reason=(
                        f"FR-015: category {cat.category!r} cites unknown "
                        f"risk ids {sorted(orphans)}"
                    ),
                    effective_labels=label_set,
                    axis_a_snapshot=axis_a,
                    axis_b_snapshot=axis_b,
                    axis_d_snapshot=axis_d,
                    effect_class=effect_class,
                )

    # Sub-phase H / Demo #8 — clearance + integrity floor (FR-008/FR-004).
    # Clearance: if axis_a carries a category whose resolved tier
    # exceeds clearance_max_tier, refuse (BLP read-up). Integrity:
    # if integrity_floor_level is set and any axis_b entry is below
    # the floor, refuse (Biba read-down).
    if clearance_max_tier is not None and axis_a is not None:
        from capabledeputy.policy.tiers import compare as _tier_compare

        for cat in axis_a.categories:
            if _tier_compare(cat.tier, clearance_max_tier) > 0:
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule=CLEARANCE_REFUSED_RULE,
                    reason=(
                        f"FR-008: profile clearance {clearance_max_tier.value} "
                        f"refuses read of category {cat.category!r} at tier "
                        f"{cat.tier.value}"
                    ),
                    effective_labels=label_set,
                    axis_a_snapshot=axis_a,
                    axis_b_snapshot=axis_b,
                    axis_d_snapshot=axis_d,
                    effect_class=effect_class,
                )

    if integrity_floor_level is not None and axis_b is not None:
        from capabledeputy.policy.resolution import (
            IntegrityFloorError,
            check_integrity_floor,
        )

        for entry in axis_b.entries:
            try:
                check_integrity_floor(
                    floor_level=integrity_floor_level,
                    input_level=entry.level.value,
                )
            except IntegrityFloorError as e:
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule=INTEGRITY_FLOOR_REFUSED_RULE,
                    reason=str(e),
                    effective_labels=label_set,
                    axis_a_snapshot=axis_a,
                    axis_b_snapshot=axis_b,
                    axis_d_snapshot=axis_d,
                    effect_class=effect_class,
                )

    # Sub-phase F / Demo #1 — envelope dial (FR-030 / SC-010).
    # Look up the cell envelope and select an outcome via the
    # operator's risk_preference. Hard-floor envelopes are immovable
    # by construction (degenerate envelopes; see policy/envelope.py).
    envelope_outcome: Decision | None = None
    envelope_rule: str | None = None
    envelope_reason: str | None = None
    # The cell key uses the FIRST axis-A category (most-specific
    # post-resolution); decision_context_canonical is the initiator
    # string for stability. Multi-category sessions produce one
    # envelope lookup per category — future work.
    if (
        envelope_set is not None
        and risk_preference is not None
        and effect_class is not None
        and axis_a is not None
        and axis_d is not None
        and effective_reversibility is not None
        and axis_a.categories
    ):
        cell = CellKey(
            category=axis_a.categories[0].category,
            effect=effect_class,
            decision_context_canonical=axis_d.initiator,
            reversibility=effective_reversibility.degree.value,
        )
        envelope = envelope_set.lookup(cell)
        if envelope is not None:
            selected = envelope.select(risk_preference)
            envelope_outcome = _V2_TO_LEGACY[selected]
            envelope_rule = f"{ENVELOPE_DIAL_RULE}:{risk_preference.value}->{selected.value}"
            envelope_reason = (
                f"envelope[{cell.category}/{cell.effect}/"
                f"{cell.decision_context_canonical}/{cell.reversibility}] "
                f"@ dial={risk_preference.value} -> {selected.value}"
            )

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
        revoked_audit_ids,
    )
    if (
        axis_a is None
        or axis_b is None
        or axis_d is None
        or effect_class is None
        or rules_v2 is None
    ):
        # Even on the legacy-only path, the reversibility / envelope
        # gates apply when the caller supplied them.
        result = legacy
        if reversibility_outcome is not None:
            result = _compose_with_reversibility(
                result,
                reversibility_outcome=reversibility_outcome,
                reversibility_rule=reversibility_rule,
                reversibility_reason=reversibility_reason,
            )
        if envelope_outcome is not None:
            result = _compose_with_envelope(
                result,
                envelope_outcome=envelope_outcome,
                envelope_rule=envelope_rule,
                envelope_reason=envelope_reason,
            )
        return result
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
    if envelope_outcome is not None:
        composed = _compose_with_envelope(
            composed,
            envelope_outcome=envelope_outcome,
            envelope_rule=envelope_rule,
            envelope_reason=envelope_reason,
        )
    return replace(
        composed,
        axis_a_snapshot=axis_a,
        axis_b_snapshot=axis_b,
        axis_d_snapshot=axis_d,
        effect_class=effect_class,
    )


def decide(*args, **kwargs) -> PolicyDecision:
    """Public chokepoint. Wraps `_decide_impl` and adds recovery-step
    synthesis (Issue #3) for non-ALLOW outcomes.

    The synthesizer maps `decision.rule` + action context to literal
    pasteable slash commands. The REPL renders them in place of the
    static prose hints from `presentation.DENY_RECOVERY`; the
    `policy.preview` tool surfaces them in its output dict so the
    agent quotes from the engine instead of inventing commands.

    Callers that need to introspect the bare decision without
    recovery noise can call `_decide_impl` directly (engine-internal).
    """
    result = _decide_impl(*args, **kwargs)
    # ALLOW outcomes don't need recovery. Already-populated results
    # (e.g. tests that construct PolicyDecision manually) get
    # preserved; only synthesize when the slot is empty.
    if result.decision is Decision.ALLOW or result.recovery_steps:
        return result
    # `action` is the third positional arg or `action=` kwarg.
    if len(args) >= 3:
        action_obj = args[2]
    else:
        action_obj = kwargs.get("action")
    if action_obj is None:
        return result
    steps = _synthesize_recovery_steps(
        decision=result.decision,
        rule=result.rule,
        action=action_obj,
        effect_class=result.effect_class or kwargs.get("effect_class"),
        reason=result.reason,
    )
    if steps:
        return replace(result, recovery_steps=steps)
    return result


def _compose_with_envelope(
    base: PolicyDecision,
    *,
    envelope_outcome: Decision,
    envelope_rule: str | None,
    envelope_reason: str | None,
) -> PolicyDecision:
    """Compose the envelope-dial outcome with the base decision.
    Most-restrictive wins. The dial NEVER relaxes a stricter base —
    SC-010 invariant: hard-floor cells immovable by the dial.
    """
    if _LEGACY_RANK[envelope_outcome] < _LEGACY_RANK[base.decision]:
        return replace(
            base,
            decision=envelope_outcome,
            rule=envelope_rule,
            reason=envelope_reason,
        )
    return base


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
