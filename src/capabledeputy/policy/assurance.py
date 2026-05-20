"""Assurance deltas — risk citations, residual-risk thresholds,
control-plane reflexivity, reversibility-weighted gating
(003 US4 / FR-015/016/018/019).

These are the four invariants US4 ratifies. Each is exposed as a
pure-function decision so engine.decide() (and tests) can compose
them without coupling to the audit emitter.

  - validate_label_citation:
    Every label MUST cite >=1 known risk-register id (FR-015).
    Orphan citations refuse at decide time.

  - should_emit_residual_risk:
    An ALLOW outcome whose composed risk-id set intersects the
    operator's "threshold-crossing" risk ids emits exactly one
    Residual-Risk Exception event (FR-016 / SC-007).

  - control_plane_admissible:
    ADMINISTER-class effects (label/capability/profile/audit edits)
    refuse when the session carries any external-untrusted
    provenance (FR-018 / SC-005). A tainted session cannot edit
    the policy oracle that gates it.

  - reversibility_gate:
    Replaces the binary destructive-op gate with a graded one
    (FR-019). Social-commitment effects are hard-coded irreversible
    regardless of mechanical reversibility — a sent message cannot
    be unsent even if a system retraction exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from capabledeputy.policy.labels import AxisB, ProvenanceLevel
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)
from capabledeputy.policy.risk_register import RiskRegister


class AssuranceError(RuntimeError):
    """Assurance invariant violation — fail-closed per Principle VI.
    Raised when a label/decision references an unknown risk-register
    id or a control-plane attempt comes from a tainted session."""


# --- FR-015: risk-id citation validation -----------------------------


def validate_label_citation(
    *,
    risk_ids: tuple[str, ...],
    register: RiskRegister,
) -> tuple[str, ...]:
    """Return the subset of `risk_ids` that are NOT in the register.
    Empty result ⇒ all citations valid. Non-empty ⇒ orphan citation;
    caller is expected to refuse the operation (FR-015 fail-closed).

    A label with zero risk_ids is *also* an orphan in the SC-001
    sense — the lint catches that at CI; this runtime check exists
    to refuse at decide() time too."""
    if not risk_ids:
        return ("<no risk_ids cited>",)
    return tuple(rid for rid in risk_ids if not register.exists(rid))


# --- FR-016: residual-risk threshold emission ------------------------


@dataclass(frozen=True)
class ResidualRiskThresholds:
    """Operator-curated set of risk-register ids that, when present
    on an ALLOW decision, MUST emit a Residual-Risk Exception event
    capturing the full inputs and rationale (FR-016)."""

    threshold_risk_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ResidualRiskSignal:
    """Result of a residual-risk evaluation.

    `should_emit` is True iff the decision was an ALLOW AND its
    composed risk-id set intersects the thresholds. `crossed`
    enumerates the matching ids for the event payload.
    """

    should_emit: bool
    crossed: tuple[str, ...]


def should_emit_residual_risk(
    *,
    decision_is_allow: bool,
    decision_risk_ids: tuple[str, ...],
    thresholds: ResidualRiskThresholds,
) -> ResidualRiskSignal:
    """Pure-function check: did the decision cross a configured
    threshold? Only ALLOW outcomes can cross — DENY / REQUIRE_APPROVAL
    don't constitute residual risk (the gate caught them).

    Exactly one event per crossing (SC-007 — caller must dedupe per
    decision). The caller (engine.decide() emit path) is responsible
    for not double-emitting on the same audit cycle.
    """
    if not decision_is_allow:
        return ResidualRiskSignal(should_emit=False, crossed=())
    crossed = tuple(rid for rid in decision_risk_ids if rid in thresholds.threshold_risk_ids)
    return ResidualRiskSignal(should_emit=bool(crossed), crossed=crossed)


# --- FR-018: control-plane reflexivity -------------------------------


class ControlPlaneEffect(StrEnum):
    """Effect classes that touch the policy oracle itself —
    ADMINISTER-class in the spec. A tainted session cannot exercise
    any of these (FR-018 / SC-005). The list is exhaustive: anything
    not here is a data-plane effect."""

    LABEL_EDIT = "administer.label_edit"
    CAPABILITY_EDIT = "administer.capability_edit"
    PROFILE_EDIT = "administer.profile_edit"
    AUDIT_EDIT = "administer.audit_edit"
    RULE_EDIT = "administer.rule_edit"
    BINDING_EDIT = "administer.binding_edit"
    OVERRIDE_POLICY_EDIT = "administer.override_policy_edit"


_CONTROL_PLANE_EFFECTS: frozenset[str] = frozenset(e.value for e in ControlPlaneEffect)


def is_control_plane_effect(effect_class: str) -> bool:
    return effect_class in _CONTROL_PLANE_EFFECTS


def control_plane_admissible(
    *,
    effect_class: str,
    axis_b: AxisB,
) -> bool:
    """True iff the effect is admissible on the control plane for
    this session's provenance posture. Non-control-plane effects are
    always admissible by this check (other gates apply).

    The rule (FR-018): if any AxisB entry is external-untrusted, the
    session is tainted and cannot reach the control plane. Otherwise
    admissible. A session that has no AxisB at all (empty entries)
    is considered untainted (the default ingest path; the bind step
    is what raises taint)."""
    if not is_control_plane_effect(effect_class):
        return True
    return all(entry.level is not ProvenanceLevel.EXTERNAL_UNTRUSTED for entry in axis_b.entries)


# --- FR-019: reversibility-weighted gating ---------------------------


class EffectGate(StrEnum):
    """The outcome of a reversibility-weighted gate.
    AUTO_OK ⇒ may auto-execute given a matched capability.
    REQUIRE_APPROVAL ⇒ surface to operator with the reversibility
    rationale.
    DENY ⇒ effective reversibility too low / agent too distant; no
    approval can rescue (caller may still escalate to override)."""

    AUTO_OK = "auto_ok"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


# Social-commitment effects are hard-coded irreversible (FR-019):
# even if the underlying mechanism technically supports retraction,
# the reputational fact of "the message was sent" cannot be undone.
_SOCIAL_COMMITMENT_EFFECTS: frozenset[str] = frozenset(
    {
        "social.send_email",
        "social.post_public",
        "social.send_message",
        "social.commit_promise",
    },
)


def is_social_commitment(effect_class: str) -> bool:
    return effect_class in _SOCIAL_COMMITMENT_EFFECTS


def reversibility_gate(
    *,
    effect_class: str,
    declared_reversibility: ReversibilityLabel,
) -> tuple[EffectGate, ReversibilityLabel, str]:
    """Pure-function gate.

    1. If the effect is in _SOCIAL_COMMITMENT_EFFECTS, force
       reversibility to (irreversible, external) regardless of the
       declared label — FR-019 hard rule.
    2. Otherwise use the declared reversibility.
    3. Map to an EffectGate:
       - reversible + system ⇒ AUTO_OK (mirrors optimistic-execution
         module; this is the gate's lower bound).
       - reversible-with-friction OR (reversible + non-system) ⇒
         REQUIRE_APPROVAL.
       - irreversible ⇒ DENY (escalate to override path if needed).
    """
    eff = declared_reversibility
    rationale = "declared reversibility"
    if is_social_commitment(effect_class):
        eff = ReversibilityLabel(
            degree=ReversibilityDegree.IRREVERSIBLE,
            agent=ReversalAgent.EXTERNAL,
        )
        rationale = f"social-commitment {effect_class!r} forced irreversible per FR-019"

    if eff.degree is ReversibilityDegree.IRREVERSIBLE:
        return EffectGate.DENY, eff, rationale
    if eff.degree is ReversibilityDegree.REVERSIBLE and eff.agent is ReversalAgent.SYSTEM:
        return EffectGate.AUTO_OK, eff, rationale
    # All remaining cases — reversible-with-friction, or
    # reversible/(human|external) — require human approval.
    return EffectGate.REQUIRE_APPROVAL, eff, rationale
