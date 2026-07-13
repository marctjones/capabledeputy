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
from capabledeputy.policy.assurance import (
    EffectGate,
    is_communication_egress,
    reversibility_gate,
)
from capabledeputy.policy.bindings import BindingError, BindingSet
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    kind_name,
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
from capabledeputy.policy.labels import (
    AxisD,
    LabelState,
    ProvenanceLevel,
)
from capabledeputy.policy.optimistic import evaluate_optimistic
from capabledeputy.policy.overrides import OverrideGrantStore, use_override
from capabledeputy.policy.reversibility import ReversibilityLabel
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier, is_above, max_of

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
DEVBOX_NO_MANAGER_RULE = "devbox-no-manager"
ORPHAN_RISK_CITATION_RULE = "orphan-risk-citation"
FIRST_USE_OF_KIND_RULE = "first-use-of-kind"

# Four-axis information-flow conflict invariants (label-model-redesign
# §R4c, decision D-conflict). These port the four co-presence conflict
# rules off the flat `Label` set onto the propagating axes. They are
# NOT Brewer-Nash / Chinese-Wall COI rules (despite the legacy
# rules.py docstring): the first is an Axis-B(provenance)xAxis-C(effect)
# *integrity* invariant (a confused-deputy / taint-flow block); the
# other three are Axis-A(category)xAxis-C(effect) *confidentiality
# confinement* invariants. They are always-on engine invariants — no
# config can disable them — sitting beside the BLP-clearance and
# Biba-floor gates. Rule ids are kept identical to the flat
# CONFLICT_RULES for audit-trail / replay continuity.
PROVENANCE_EGRESS_RULE = "untrusted-meets-egress"
HEALTH_EGRESS_RULE = "health-meets-egress"
FINANCIAL_EMAIL_RULE = "financial-meets-email"
FINANCIAL_PURCHASE_RULE = "financial-meets-purchase"
# Destination-aware web.fetch egress floor (#293/#296). A URL-target web.fetch
# is an outbound request whose destination the planner controls. When the
# destination is NOT operator-allowlisted (no BindingSet match), a session
# carrying confidential data cannot silently exfiltrate it: restricted-tier data
# is a structural DENY, regulated/sensitive-tier data requires human approval on
# the host. An allowlisted destination is safe routing (composes with Pattern 3)
# and is not gated by this floor. A clean session (no confidential category)
# fetches freely — web research is preserved.
FETCH_URL_RESTRICTED_RULE = "confidential-meets-web-fetch"
FETCH_URL_REGULATED_RULE = "regulated-data-meets-web-fetch"

# Cookbook §4 #6 — capability kinds that fire a first-use prompt
# when `Session.first_use_prompt_enabled` is on. Reads are excluded
# (they don't change state and would create approval fatigue from
# every new mailbox label / file path). The set covers everything
# with egress, purchase, destructive, or execute semantics — the
# operator's first use of the authority is the right approval
# moment for confirming intent.
_PROMPTABLE_FIRST_USE_KINDS: frozenset[CapabilityKind] = frozenset(
    {
        CapabilityKind.SEND_EMAIL,
        CapabilityKind.SEND_MESSAGE,
        CapabilityKind.QUEUE_PURCHASE,
        CapabilityKind.BROWSER_AUTOMATION,
        CapabilityKind.BROWSER_NAVIGATE,
        CapabilityKind.BROWSER_INTERACT,
        CapabilityKind.BROWSER_SCRIPT,
        CapabilityKind.BROWSER_FILE,
        CapabilityKind.MACOS_AUTOMATION,
        CapabilityKind.MACOS_APP_CONTROL,
        CapabilityKind.MACOS_CLIPBOARD_WRITE,
        CapabilityKind.APPLE_MAIL_DRAFT,
        CapabilityKind.GMAIL_DRAFT,
        CapabilityKind.KEYNOTE_PRESENT,
        CapabilityKind.PAGES_EDIT,
        CapabilityKind.PAGES_EXPORT,
        CapabilityKind.NUMBERS_EDIT,
        CapabilityKind.NUMBERS_EXPORT,
        CapabilityKind.WRITE_FS,
        CapabilityKind.CREATE_FS,
        CapabilityKind.MODIFY_FS,
        CapabilityKind.DELETE_FS,
        CapabilityKind.CALENDAR_WRITE,
        CapabilityKind.CREATE_CAL,
        CapabilityKind.MODIFY_CAL,
        CapabilityKind.DELETE_CAL,
        CapabilityKind.EXECUTE_SANDBOX,
        CapabilityKind.EXECUTE_DEVBOX,
    },
)

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


def _target_uses_binding_namespace(target: str) -> bool:
    """True when a target is a canonical location, not a principal id."""

    return target.startswith("/") or "://" in target or target.startswith("mcp:")


def _target_is_remote_url(target: str) -> bool:
    """True when the action target is an outbound http(s) URL.

    Distinguishes web.fetch (target = an LLM-chosen URL, an outbound request
    that can carry data out via the path/query/body) from web.search (target =
    a query string sent to a fixed provider). Both share CapabilityKind
    WEB_FETCH, so the egress floor must gate on the target shape, not the kind
    alone (#293)."""
    return target.startswith(("http://", "https://"))


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
    # the logged event alone (FR-021). R4b.4: labels_snapshot replaces
    # axis_a_snapshot + axis_b_snapshot.
    labels_snapshot: LabelState | None = None
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
    Decision.WARN: 3,
    Decision.ALLOW: 4,
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


def _conflict_invariant_outcome(
    labels: LabelState,
    action: Action,
) -> tuple[Decision, str, str] | None:
    """Four-axis port of the flat CONFLICT_RULES (decision D-conflict).

    Computes the four always-on information-flow conflict outcomes from
    the propagating axes instead of the flat `Label` set:

      1. Axis-B `external-untrusted` provenance + egress   ⇒ DENY
         (integrity / confused-deputy: tainted data cannot leave).
      2. Axis-A `health` category + egress                 ⇒ DENY
      3. Axis-A `financial` category + communication/browser egress ⇒ DENY
      4. Axis-A `financial` category + purchase egress      ⇒ REQUIRE_APPROVAL
         (2-4: confidentiality confinement).

    Egress is the action kind: SEND_EMAIL / SEND_MESSAGE / QUEUE_PURCHASE,
    plus active browser-control kinds because they can submit data to
    arbitrary remote sites. Browser read-only observation is deliberately
    excluded. URL-target web.fetch egress is handled separately by the
    destination-aware, tier-graded `_fetch_url_egress_outcome` floor
    (#293/#296). Rules are evaluated in precedence order; the first firing
    wins. Returns (decision, rule_id, reason) or None.
    """
    is_email = action.kind == CapabilityKind.SEND_EMAIL
    is_message = action.kind == CapabilityKind.SEND_MESSAGE
    is_purchase = action.kind == CapabilityKind.QUEUE_PURCHASE
    is_browser = action.kind in {
        CapabilityKind.BROWSER_AUTOMATION,
        CapabilityKind.BROWSER_NAVIGATE,
        CapabilityKind.BROWSER_INTERACT,
        CapabilityKind.BROWSER_SCRIPT,
        CapabilityKind.BROWSER_FILE,
    }
    # NB: URL-target web.fetch egress is handled by the destination-aware
    # `_fetch_url_egress_outcome` floor (#293/#296), NOT here — it needs the
    # allowlist signal and tier grading, and must not be subject to the
    # external-untrusted leg below (a fetch taints the session
    # external-untrusted, so gating untrusted+fetch would kill research).
    if not (is_email or is_message or is_purchase or is_browser):
        return None
    if is_email:
        egress = "email"
    elif is_message:
        egress = "message"
    elif is_browser:
        egress = "browser automation"
    else:
        egress = "purchase"
    provenance = {e.level for e in labels.b}
    categories = {c.category for c in labels.a}
    if ProvenanceLevel.EXTERNAL_UNTRUSTED in provenance:
        return (
            Decision.DENY,
            PROVENANCE_EGRESS_RULE,
            f"{PROVENANCE_EGRESS_RULE}: external-untrusted provenance cannot "
            f"egress via {egress} (integrity / confused-deputy)",
        )
    if "health" in categories:
        return (
            Decision.DENY,
            HEALTH_EGRESS_RULE,
            f"{HEALTH_EGRESS_RULE}: health-category data cannot egress via {egress}",
        )
    if "financial" in categories:
        if is_email or is_message or is_browser:
            return (
                Decision.DENY,
                FINANCIAL_EMAIL_RULE,
                f"{FINANCIAL_EMAIL_RULE}: financial-category data cannot egress via {egress}",
            )
        return (
            Decision.REQUIRE_APPROVAL,
            FINANCIAL_PURCHASE_RULE,
            f"{FINANCIAL_PURCHASE_RULE}: financial-category data egress via "
            f"purchase requires approval",
        )
    return None


def _fetch_url_egress_outcome(
    labels: LabelState,
    action: Action,
    *,
    destination_allowlisted: bool,
) -> tuple[Decision, str, str] | None:
    """Destination-aware confidentiality floor for URL-target web.fetch (#293/#296).

    A `web.fetch` to an LLM-chosen http(s) URL is an outbound request whose
    destination the planner controls; the path/query/body can carry read data
    to an arbitrary host. This is the exfiltration channel that shares
    CapabilityKind.WEB_FETCH with `web.search` (whose target is a query string,
    not a URL, so it is exempt here).

    The gate keys on DESTINATION, not category alone, so it composes with
    Pattern 3 (reference-handle routing): an operator-allowlisted destination is
    safe routing and is not gated, whether the value is held directly or bound
    from a handle. Absent an allowlist match, a session carrying confidential
    data is gated by the highest confidential-category TIER present:

      - restricted / prohibited (health, financial, credentials) ⇒ DENY
        (structural — matches the other egress channels' confidentiality floor).
      - regulated (personal, proprietary_work)                    ⇒ REQUIRE_APPROVAL
        (no longer *silent*: a human decides on the destination host. This is a
        human-judgment gate on the recipient, not a structural guarantee — a
        reviewer cannot evaluate an opaque query string, but can evaluate the
        host).
      - sensitive and below (low-stakes working labels like `news`) ⇒ no gate.

    A clean session (no confidential Axis-A category) or a session carrying only
    sensitive-or-below labels fetches freely — web research and content
    gathering are preserved. The external-untrusted provenance a fetch itself
    attaches is Axis-B, not a confidential category, so multi-page research is
    unaffected.

    KNOWN LIMIT (out of scope, tracked): principal-typed secrets pasted into
    chat carry no Axis-A label, so a fetch does not gate on them — this closure
    is for *labeled* confidential data. That is the labeling-oracle gap, not a
    fetch-specific one.
    """
    if action.kind != CapabilityKind.WEB_FETCH or not _target_is_remote_url(action.target):
        return None
    if destination_allowlisted:
        return None
    if not labels.a:
        return None  # clean session — research is preserved
    highest = max_of(*(tag.tier for tag in labels.a))
    # Threshold is REGULATED: the registered high-confidentiality categories
    # (personal, proprietary_work = regulated; health, financial, credentials =
    # restricted) are all >= regulated. SENSITIVE and below are low-stakes
    # working labels (e.g. `news` on fetched articles) — gating those would make
    # ordinary content-gathering (fetch more pages into a labeled session) an
    # approval-fatigue trap, with no real exfil benefit.
    if not is_above(highest, Tier.SENSITIVE):
        return None  # sensitive-or-below — not gated
    if is_above(highest, Tier.REGULATED):  # restricted / prohibited
        return (
            Decision.DENY,
            FETCH_URL_RESTRICTED_RULE,
            f"{FETCH_URL_RESTRICTED_RULE}: restricted-tier data cannot egress via a "
            "web.fetch to a non-allowlisted destination",
        )
    return (
        Decision.REQUIRE_APPROVAL,
        FETCH_URL_REGULATED_RULE,
        f"{FETCH_URL_REGULATED_RULE}: confidential data egress via a web.fetch to a "
        "non-allowlisted destination requires human approval of the destination host",
    )


def _compose_with_conflict_invariant(
    base: PolicyDecision,
    outcome: tuple[Decision, str, str] | None,
    *,
    crossed_floors: frozenset[str] = frozenset(),
    personal: bool = False,
) -> PolicyDecision:
    """Compose a four-axis conflict-invariant outcome with the base
    decision. Most-restrictive wins (these are floors, like the
    envelope/reversibility composers); they never relax a stricter
    base. A no-op when no invariant fired.

    Slice C (FR-049): under the `personal` trust profile, a human-
    ratified rule that explicitly named this structural floor in
    `crosses_floor` SUPPRESSES the floor — the rule's relaxation over the
    operator's OWN data stands. Two hard guards make this safe:
      - It is gated on `personal`; `managed` never suppresses (the floor
        re-applies exactly as before).
      - `untrusted-meets-egress` is NEVER suppressible here, even if its id
        somehow reached `crossed_floors` — defense in depth on top of the
        load-time refusal in decision_rules._validate_crosses_floor. A
        standing rule can never auto-cross the untrusted floor.
    """
    if outcome is None:
        return base
    decision, rule, reason = outcome
    if (
        personal
        and rule in crossed_floors
        and rule != PROVENANCE_EGRESS_RULE  # untrusted floor never rule-crossable
    ):
        return base
    if _LEGACY_RANK[decision] < _LEGACY_RANK[base.decision]:
        return replace(base, decision=decision, rule=rule, reason=reason)
    return base


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
    capabilities: frozenset[Capability],
    action: Action,
    used_kinds: frozenset[CapabilityKind] = frozenset(),
    now: datetime | None = None,
    cap_uses: dict[str, tuple[datetime, ...]] | None = None,
    revoked_audit_ids: frozenset[UUID] = frozenset(),
    rate_limit_escalation: bool = False,
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
                    f"capability for {kind_name(action.kind)}({action.target}) "
                    f"expired at {deadline.isoformat()} "
                    f"(decision time {eff_now.isoformat()})"
                ),
                matched_capability=expired_match,
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
            # Cookbook P2.6 — rate-limit-as-friction. When the
            # operator's risk dial is balanced/aggressive, a rate-
            # exceeded match escalates to REQUIRE_APPROVAL instead
            # of DENY: the operator can vouch mid-stream (approve
            # the Nth send) instead of losing the session to a
            # hard deny. Cautious sessions keep the hard floor —
            # the cap is a non-negotiable limit, not a tripwire.
            outcome = Decision.REQUIRE_APPROVAL if rate_limit_escalation else Decision.DENY
            reason_prefix = (
                "rate limit exceeded — operator approval required"
                if rate_limit_escalation
                else "rate limit exceeded"
            )
            return PolicyDecision(
                decision=outcome,
                rule=RATE_LIMIT_EXCEEDED_RULE,
                reason=(
                    f"capability for {kind_name(action.kind)}({action.target}) "
                    f"{reason_prefix}: {rl.max_uses} uses per "
                    f"{rl.window_seconds}s (decision time "
                    f"{eff_now.isoformat()})"
                ),
                matched_capability=rate_match,
            )
        return PolicyDecision(
            decision=Decision.DENY,
            reason=f"no matching capability for {kind_name(action.kind)}({action.target})",
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
                f"capability for {kind_name(action.kind)}({action.target}) "
                f"is cascaded-inert: originating ancestor "
                f"audit_id={originator_id} (revoked/expired/exhausted)"
            ),
            matched_capability=cap,
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
                f"capability for {kind_name(action.kind)} was revoked by prior use of "
                f"{sorted(k.value for k in revoking)}"
            ),
            matched_capability=cap,
        )

    # Destructive-op gate (DESIGN.md §7.5): MODIFY_* / DELETE_* actions
    # require a capability with `allows_destructive=True` OR an approval.
    # The default (`allows_destructive=False`) routes the action through
    # REQUIRE_APPROVAL so a human authorises it. This codifies the
    # Clark-Wilson well-formed-transaction principle: modifications are
    # deliberate, audited acts; never the implicit byproduct of a flow.
    # Issue #35 — destructive check now consults both the built-in
    # DESTRUCTIVE_KINDS set AND the CustomKindRegistry's per-kind flag,
    # so a custom kind declared with `destructive: true` in servers.d/
    # gets the same Clark-Wilson gating.
    from capabledeputy.policy.capabilities import is_destructive_kind

    if is_destructive_kind(action.kind) and not cap.allows_destructive:
        # action.kind may be enum (has .value) or str (custom kinds).
        kind_str = kind_name(action.kind)
        return PolicyDecision(
            decision=Decision.REQUIRE_APPROVAL,
            rule=DESTRUCTIVE_OP_RULE,
            reason=(
                f"{kind_str} on '{action.target}' is a destructive "
                "operation and the matched capability does not have "
                "allows_destructive=True"
            ),
            matched_capability=cap,
        )

    return PolicyDecision(
        decision=Decision.ALLOW,
        matched_capability=cap,
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
    if decision in {Decision.ALLOW, Decision.WARN}:
        return ()

    kind = kind_name(action.kind) if action.kind else "READ_FS"
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
                rationale=(
                    "Capability was revoked by a prior tool use; fresh session resets the record."
                ),
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
                rationale="Session is tainted by prior reads of untrusted/sensitive content; a fresh session has no labels to conflict.",  # noqa: E501
            ),
            RecoveryStep(
                command="/grant",
                args=(kind, target, "--one-shot"),
                rationale="Grant the capability in the fresh session.",
            ),
            RecoveryStep(
                command="/override",
                args=(
                    "request",
                    kind,
                    target,
                    "--justification",
                    f'"explicit user authorization for {kind} on {target}"',
                ),
                rationale="Alternative: request operator override to bypass the label conflict in this session.",  # noqa: E501
            ),
        )

    # v2 relax-refused — operator's policy floor blocks the requested
    # relaxation. Same shape as label conflicts — clean session or
    # override path.
    if rule == RELAX_REFUSED_RULE:
        return (
            RecoveryStep(
                command="/override",
                args=(
                    "request",
                    kind,
                    target,
                    "--justification",
                    '"operator-floor relaxation needed"',
                ),
                rationale="A v2 rule refused to relax to ALLOW; operator override is the only path.",  # noqa: E501
            ),
        )

    # Unknown rule — synthesizer doesn't know how to recover.
    return ()


def _egress_needs_override(
    labels: LabelState | None,
    override_categories: frozenset[str],
    override_tiers: frozenset[str],
) -> bool:
    """True iff the session carries operator-configured super-sensitive data
    (by category or by tier) that escalates communication egress from
    APPROVAL to OVERRIDE_REQUIRED. Empty config ⇒ never (approval default)."""
    if labels is None or (not override_categories and not override_tiers):
        return False
    return any(
        tag.category in override_categories
        or getattr(tag.tier, "value", str(tag.tier)) in override_tiers
        for tag in labels.a
    )


def _decide_impl(
    capabilities: frozenset[Capability],
    action: Action,
    used_kinds: frozenset[CapabilityKind] = frozenset(),
    now: datetime | None = None,
    cap_uses: dict[str, tuple[datetime, ...]] | None = None,
    *,
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
    devbox_manager_wired: bool = False,
    revoked_audit_ids: frozenset[UUID] = frozenset(),
    first_use_prompt_enabled: bool = False,
    rate_limit_escalation: bool = False,
    labels: LabelState | None = None,
    egress_override_categories: frozenset[str] = frozenset(),
    egress_override_tiers: frozenset[str] = frozenset(),
    trust_profile_is_personal: bool = False,
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
    # R4b.4 — use bundled LabelState directly. Default to empty when not provided.
    if labels is None:
        labels = LabelState()

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
                # FR-036 single-use: consume the grant so a subsequent
                # decide() falls back to the normal policy. For a GROUP grant
                # (slice D) this consumes only THIS member; the grant stays
                # ACTIVE for the rest of the batch until every member is used.
                consumed = active.consume_for(
                    action_kind=action.kind,
                    target=action.target,
                    now=eff_now,
                )
                override_grants.update(consumed)
                return PolicyDecision(
                    decision=Decision.ALLOW,
                    rule="override-grant-active",
                    reason=(
                        f"override grant {active.id} authorizes "
                        f"{kind_name(action.kind)}({action.target}) until "
                        f"{active.expires_at.isoformat()}"
                    ),
                    matched_capability=mint_result,
                )

    # T077 / Demo #6 — binding canonicalization. When the operator
    # has wired a BindingSet, every write/egress destination must
    # canonicalize through it. An unbound or non-canonicalizable
    # target ⇒ refuse (FR-023 / FR-048 / SC-022). On success, the
    # canonical destination id replaces action.target for the v2
    # rule predicate so model-controlled case-varying inputs cannot
    # bypass rules authored against the canonical form.
    canonical_target: str | None = None
    if bindings is not None and action.target and _target_uses_binding_namespace(action.target):
        try:
            resolution = bindings.resolve(action.target)
            canonical_target = resolution.canonical_destination_id
        except BindingError as e:
            return PolicyDecision(
                decision=Decision.DENY,
                rule=BINDING_UNBOUND_RULE,
                reason=str(e),
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
            # Configurable egress escalation (FR-019 amended). Irreversible
            # COMMUNICATION egress (sending a message) is a POLICY gate, not
            # a structural floor: by default it routes to human APPROVAL
            # (approve-at-the-moment), and operator-configured super-sensitive
            # data escalates to OVERRIDE_REQUIRED (pre-authorize). Purchases /
            # commitments and all other irreversible effects keep hard DENY.
            # (Structural floors — BLP/Biba/conflict invariants — still
            # compose most-restrictively and win, e.g. health-meets-egress.)
            if is_communication_egress(effect_class):
                if _egress_needs_override(
                    labels,
                    egress_override_categories,
                    egress_override_tiers,
                ):
                    reversibility_outcome = Decision.OVERRIDE_REQUIRED
                    reversibility_rule = "egress-requires-override"
                    reversibility_reason = (
                        "super-sensitive communication egress requires a "
                        "pre-authorized override grant"
                    )
                else:
                    reversibility_outcome = Decision.REQUIRE_APPROVAL
                    reversibility_rule = "egress-requires-approval"
                    reversibility_reason = (
                        "irreversible communication egress requires human approval"
                    )
            else:
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
    # A tainted session (carries external-untrusted provenance) cannot
    # exercise any ADMINISTER-class effect. Refused before legacy/v2
    # so the audit reason is unambiguous.
    if effect_class is not None:
        from capabledeputy.policy.assurance import (
            control_plane_admissible,
            is_control_plane_effect,
        )

        if is_control_plane_effect(effect_class) and not control_plane_admissible(
            effect_class=effect_class,
            labels=labels,
        ):
            return PolicyDecision(
                decision=Decision.DENY,
                rule=CONTROL_PLANE_TAINTED_RULE,
                reason=(
                    f"FR-018: session with external-untrusted provenance "
                    f"cannot exercise control-plane effect {effect_class!r}"
                ),
                labels_snapshot=labels,
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
            labels_snapshot=labels,
            axis_d_snapshot=axis_d,
            effect_class=effect_class,
        )

    # Devbox-without-manager — same fail-closed shape for the
    # persistent-container effect class. Defense-in-depth: the
    # tool-registration check in tools/native/devbox.py already
    # collapses the tool list when no manager is wired, but if a
    # devbox-shaped tool exists through other paths (custom kind,
    # operator misconfig) the engine refuses here. Parallels the
    # SANDBOX_NO_ACTUATOR_RULE gate above.
    if (
        effect_class is not None
        and effect_class.lower().startswith("execute.devbox")
        and not devbox_manager_wired
    ):
        return PolicyDecision(
            decision=Decision.OVERRIDE_REQUIRED,
            rule=DEVBOX_NO_MANAGER_RULE,
            reason=(
                f"{effect_class!r} requires a PodmanDevbox manager; "
                "none wired. Declare a sandbox.regions block in "
                "daemon.yaml and ensure Podman is installed."
            ),
            labels_snapshot=labels,
            axis_d_snapshot=axis_d,
            effect_class=effect_class,
        )

    # Orphan risk-citation refusal (FR-015 runtime side). If a risk
    # register is wired AND labels declare risk_ids, any id NOT
    # in the register refuses the decision. The CI lint already catches
    # missing framework_refs at build time; this catches runtime
    # citations of unknown ids.
    if risk_register is not None:
        from capabledeputy.policy.assurance import validate_label_citation

        for cat in labels.a:
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
                    labels_snapshot=labels,
                    axis_d_snapshot=axis_d,
                    effect_class=effect_class,
                )

    # Sub-phase H / Demo #8 — clearance + integrity floor (FR-008/FR-004).
    # Clearance: if labels carry a category whose resolved tier
    # exceeds clearance_max_tier, refuse (BLP read-up). Integrity:
    # if integrity_floor_level is set and any provenance entry is below
    # the floor, refuse (Biba read-down).
    if clearance_max_tier is not None:
        from capabledeputy.policy.tiers import compare as _tier_compare

        for cat in labels.a:
            if _tier_compare(cat.tier, clearance_max_tier) > 0:
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule=CLEARANCE_REFUSED_RULE,
                    reason=(
                        f"FR-008: profile clearance {clearance_max_tier.value} "
                        f"refuses read of category {cat.category!r} at tier "
                        f"{cat.tier.value}"
                    ),
                    labels_snapshot=labels,
                    axis_d_snapshot=axis_d,
                    effect_class=effect_class,
                )

    if integrity_floor_level is not None:
        from capabledeputy.policy.resolution import (
            IntegrityFloorError,
            check_integrity_floor,
        )

        for entry in labels.b:
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
                    labels_snapshot=labels,
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
    # The cell key uses the FIRST category (most-specific
    # post-resolution); decision_context_canonical is the initiator
    # string for stability. Multi-category sessions produce one
    # envelope lookup per category — future work.
    if (
        envelope_set is not None
        and risk_preference is not None
        and effect_class is not None
        and axis_d is not None
        and effective_reversibility is not None
        and labels.a
    ):
        cell = CellKey(
            category=next(iter(labels.a)).category,
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

    # Four-axis conflict invariants (decision D-conflict / §R4c):
    # always-on integrity + confidentiality-confinement floors computed
    # from the propagating axes. Composed most-restrictively into both
    # the legacy-only and the v2 return paths below — they only ratchet
    # stricter, never relax.
    conflict_outcome = _conflict_invariant_outcome(labels, action)
    # Destination-aware web.fetch egress floor (#293/#296). A URL fetch is
    # "allowlisted" when a configured BindingSet resolved its destination above
    # (canonical_target set) — that is the operator declaring the host safe, and
    # it composes with Pattern 3 handle-routing. Absent an allowlist match, a
    # confidential-tainted session is gated by tier. Composed most-restrictively
    # after envelope/v2/inspector, exactly like conflict_outcome, so a permissive
    # dial or a relaxing rule can never dial the gate back to ALLOW.
    fetch_destination_allowlisted = canonical_target is not None and _target_is_remote_url(
        action.target,
    )
    fetch_outcome = _fetch_url_egress_outcome(
        labels,
        action,
        destination_allowlisted=fetch_destination_allowlisted,
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
                refused_relax_inputs=inspected.refused,
            )

    legacy = _decide_legacy(
        capabilities,
        action,
        used_kinds,
        now,
        cap_uses,
        revoked_audit_ids,
        rate_limit_escalation=rate_limit_escalation,
    )
    if axis_d is None or effect_class is None or rules_v2 is None:
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
        result = _compose_with_conflict_invariant(result, conflict_outcome)
        result = _compose_with_conflict_invariant(result, fetch_outcome)
        return _maybe_first_use_escalation(
            result,
            action=action,
            used_kinds=used_kinds,
            first_use_prompt_enabled=first_use_prompt_enabled,
        )
    # Thread the engine's effective clock into the v2 evaluator so
    # time-window rules (e.g. send-after-hours-require-approval) can
    # actually fire. RulePredicate.matches fails-closed when a
    # time-window rule is asked to match without a now_hour, so the
    # rule's intent is silently ignored if we omit this argument.
    _eff_now_for_v2 = now if now is not None else datetime.now(UTC)
    v2 = _evaluate_v2(
        rules=rules_v2,
        labels=labels,
        axis_d=axis_d,
        effect_class=effect_class,
        target=canonical_target if canonical_target is not None else action.target,
        default_when_no_match=default_v2_outcome,
        now_hour=_eff_now_for_v2.hour,
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
    composed = _compose_with_conflict_invariant(
        composed,
        conflict_outcome,
        crossed_floors=v2.crossed_floors,
        personal=trust_profile_is_personal,
    )
    composed = _compose_with_conflict_invariant(
        composed,
        fetch_outcome,
        crossed_floors=v2.crossed_floors,
        personal=trust_profile_is_personal,
    )
    final = replace(
        composed,
        labels_snapshot=labels,
        axis_d_snapshot=axis_d,
        effect_class=effect_class,
    )
    return _maybe_first_use_escalation(
        final,
        action=action,
        used_kinds=used_kinds,
        first_use_prompt_enabled=first_use_prompt_enabled,
    )


def _maybe_first_use_escalation(
    result: PolicyDecision,
    *,
    action: Action,
    used_kinds: frozenset[CapabilityKind],
    first_use_prompt_enabled: bool,
) -> PolicyDecision:
    """Cookbook §4 #6 — escalate an ALLOW outcome to REQUIRE_APPROVAL
    the FIRST time a session uses a promptable kind. Only touches
    ALLOWs — non-ALLOW results already gate, so the first-use
    prompt would add nothing. Reads are excluded (would be too
    noisy); the promptable set covers egress/destructive/execute.

    Once the operator approves and the action dispatches, the kind
    enters `session.used_kinds` and subsequent decisions pass through
    here unchanged."""
    if (
        first_use_prompt_enabled
        and result.decision == Decision.ALLOW
        and action.kind in _PROMPTABLE_FIRST_USE_KINDS
        and action.kind not in used_kinds
    ):
        return replace(
            result,
            decision=Decision.REQUIRE_APPROVAL,
            rule=FIRST_USE_OF_KIND_RULE,
            reason=(
                f"first use of {kind_name(action.kind)} in this session — "
                "operator confirmation required (cookbook §4 #6)"
            ),
        )
    return result


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
    # `action` is the second positional arg or `action=` kwarg.
    action_obj = args[1] if len(args) >= 2 else kwargs.get("action")
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
