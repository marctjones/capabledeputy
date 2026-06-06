"""Composition Sub-phases F+G+H — Demos #1, #7, #8 wired into decide().

#1 envelope dial (FR-030 / SC-010): the operator's risk_preference
   selects a point inside each cell's [strictest, loosest] envelope.
   Hard-floor envelopes are immovable.
#7 control-plane reflexivity (FR-018 / SC-005): a session with
   external-untrusted provenance cannot exercise an ADMINISTER-class
   effect.
#8 clearance + integrity floor (FR-008 + FR-004): max-tier clearance
   refuses read-up; integrity floor refuses below-floor input.
"""

from __future__ import annotations

from datetime import UTC, datetime

from capabledeputy.policy.actions import Action
from capabledeputy.policy.assurance import ControlPlaneEffect
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import DecisionRules, RuleOutcome
from capabledeputy.policy.engine import (
    CLEARANCE_REFUSED_RULE,
    CONTROL_PLANE_TAINTED_RULE,
    ENVELOPE_DIAL_RULE,
    INTEGRITY_FLOOR_REFUSED_RULE,
    decide,
)
from capabledeputy.policy.envelope import (
    CellKey,
    EnvelopeSet,
    OutcomeEnvelope,
    RiskPreference,
)
from capabledeputy.policy.labels import (
    AxisA,
    AxisB,
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _wide_cap(kind: CapabilityKind = CapabilityKind.WRITE_FS) -> Capability:
    return Capability(
        kind=kind,
        pattern="*",
        origin=CapabilityOrigin.USER_APPROVED,
        allows_destructive=True,
    )


def _principal_axes(category: str = "proprietary_work") -> tuple[AxisA, AxisB, AxisD]:
    axis_a = AxisA(
        categories=(CategoryTag(category=category, tier=Tier.SENSITIVE),),
    )
    axis_b = AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    axis_d = AxisD(initiator="principal:alice", authentication="device-bound")
    return axis_a, axis_b, axis_d


def _tainted_axis_b() -> AxisB:
    return AxisB(
        entries=(
            ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT),
            ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),
        ),
    )


# --- Demo #1: envelope dial -----------------------------------------


def test_dial_picks_strictest_under_cautious() -> None:
    """Cell envelope [REQUIRE_APPROVAL, AUTO]. Cautious dial picks
    REQUIRE_APPROVAL even though AUTO is in-envelope."""
    axis_a, axis_b, axis_d = _principal_axes()
    cell = CellKey(
        category="proprietary_work",
        effect="data.write_scratch",
        decision_context_canonical="principal:alice",
        reversibility="reversible",
    )
    envset = EnvelopeSet(
        by_cell={
            cell: OutcomeEnvelope(
                cell=cell,
                strictest=RuleOutcome.REQUIRE_APPROVAL,
                loosest=RuleOutcome.AUTO,
            ),
        },
    )
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=ReversibilityLabel(
            degree=ReversibilityDegree.REVERSIBLE,
            agent=ReversalAgent.SYSTEM,
        ),
        envelope_set=envset,
        risk_preference=RiskPreference.CAUTIOUS,
        now=_NOW,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.rule is not None
    assert ENVELOPE_DIAL_RULE in result.rule


def test_dial_picks_loosest_under_permissive() -> None:
    """Same envelope, permissive dial picks AUTO. But the
    reversibility gate's optimistic-auto already produced ALLOW for
    this case, so the dial doesn't change the decision."""
    axis_a, axis_b, axis_d = _principal_axes()
    cell = CellKey(
        category="proprietary_work",
        effect="data.write_scratch",
        decision_context_canonical="principal:alice",
        reversibility="reversible",
    )
    envset = EnvelopeSet(
        by_cell={
            cell: OutcomeEnvelope(
                cell=cell,
                strictest=RuleOutcome.REQUIRE_APPROVAL,
                loosest=RuleOutcome.AUTO,
            ),
        },
    )
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=ReversibilityLabel(
            degree=ReversibilityDegree.REVERSIBLE,
            agent=ReversalAgent.SYSTEM,
        ),
        envelope_set=envset,
        risk_preference=RiskPreference.PERMISSIVE,
        now=_NOW,
    )
    # Either optimistic-auto fired (rule starts with "optimistic-auto")
    # or the envelope dial relaxed to AUTO. Either way ALLOW.
    assert result.decision == Decision.ALLOW


def test_hard_floor_envelope_immovable_by_dial() -> None:
    """SC-010 — a hard-floor envelope (strictest == loosest == DENY)
    is immovable. Even permissive dial returns DENY."""
    axis_a, axis_b, axis_d = _principal_axes()
    cell = CellKey(
        category="proprietary_work",
        effect="data.write_scratch",
        decision_context_canonical="principal:alice",
        reversibility="reversible",
    )
    envset = EnvelopeSet(
        by_cell={
            cell: OutcomeEnvelope(
                cell=cell,
                strictest=RuleOutcome.DENY,
                loosest=RuleOutcome.DENY,
            ),
        },
    )
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=ReversibilityLabel(
            degree=ReversibilityDegree.REVERSIBLE,
            agent=ReversalAgent.SYSTEM,
        ),
        envelope_set=envset,
        risk_preference=RiskPreference.PERMISSIVE,  # most permissive
        now=_NOW,
    )
    assert result.decision == Decision.DENY


# --- Demo #7: control-plane reflexivity -----------------------------


def test_tainted_session_refused_for_administer_effect() -> None:
    """SC-005 — external-untrusted in AxisB ⇒ ADMINISTER effects
    refused with CONTROL_PLANE_TAINTED_RULE."""
    axis_a, _, axis_d = _principal_axes()
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=_tainted_axis_b(),
        axis_d=axis_d,
        effect_class=ControlPlaneEffect.LABEL_EDIT.value,
        rules_v2=DecisionRules(rules=()),
        now=_NOW,
    )
    assert result.decision == Decision.DENY
    assert result.rule == CONTROL_PLANE_TAINTED_RULE


def test_clean_session_allowed_for_administer_effect() -> None:
    """A clean (principal-direct only) session may exercise admin
    effects — this is a v2-default REQUIRE_APPROVAL since no rule
    matched, but NOT a DENY."""
    axis_a, axis_b, axis_d = _principal_axes()
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,  # clean
        axis_d=axis_d,
        effect_class=ControlPlaneEffect.LABEL_EDIT.value,
        rules_v2=DecisionRules(rules=()),
        now=_NOW,
    )
    assert result.decision != Decision.DENY


def test_tainted_session_data_plane_effect_unaffected() -> None:
    """Only ADMINISTER effects are gated by control-plane reflexivity.
    A data-plane effect on a tainted session still goes through the
    normal pipeline."""
    axis_a, _, axis_d = _principal_axes()
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=_tainted_axis_b(),
        axis_d=axis_d,
        effect_class="data.write_scratch",  # data-plane
        rules_v2=DecisionRules(rules=()),
        now=_NOW,
        labels=LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)})),
    )
    # No clearance gate, no envelope ⇒ v2 default SUGGEST.
    assert result.decision == Decision.REQUIRE_APPROVAL
    assert result.rule != CONTROL_PLANE_TAINTED_RULE


# --- Demo #8: clearance + integrity floor ---------------------------


def test_clearance_refuses_read_up() -> None:
    """A profile cleared to REGULATED cannot read a RESTRICTED-tier
    datum (FR-008)."""
    axis_a = AxisA(
        categories=(CategoryTag(category="health", tier=Tier.RESTRICTED),),
    )
    axis_b = AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    axis_d = AxisD(initiator="principal:alice")
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.read",
        rules_v2=DecisionRules(rules=()),
        clearance_max_tier=Tier.REGULATED,
        now=_NOW,
    )
    assert result.decision == Decision.DENY
    assert result.rule == CLEARANCE_REFUSED_RULE


def test_clearance_open_when_below_max() -> None:
    """A SENSITIVE-tier read under REGULATED clearance is fine."""
    axis_a = AxisA(
        categories=(CategoryTag(category="x", tier=Tier.SENSITIVE),),
    )
    axis_b = AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    axis_d = AxisD(initiator="principal:alice")
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.read",
        rules_v2=DecisionRules(rules=()),
        clearance_max_tier=Tier.REGULATED,
        now=_NOW,
        labels=LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)})),
    )
    assert result.decision != Decision.DENY or result.rule != CLEARANCE_REFUSED_RULE


def test_integrity_floor_refuses_below_floor_input() -> None:
    """An integrity-floored step refuses an external-untrusted input
    (FR-004 Biba no-read-down)."""
    axis_a, _, axis_d = _principal_axes()
    axis_b = AxisB(
        entries=(ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),),
    )
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.process",
        rules_v2=DecisionRules(rules=()),
        integrity_floor_level="system-internal",  # demand >= system-internal
        now=_NOW,
    )
    assert result.decision == Decision.DENY
    assert result.rule == INTEGRITY_FLOOR_REFUSED_RULE


def test_integrity_floor_admits_above_floor_input() -> None:
    """principal-direct is ABOVE the system-internal floor — admitted."""
    axis_a, axis_b, axis_d = _principal_axes()
    result = decide(
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.process",
        rules_v2=DecisionRules(rules=()),
        integrity_floor_level="system-internal",
        now=_NOW,
        labels=LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)})),
    )
    assert result.rule != INTEGRITY_FLOOR_REFUSED_RULE
