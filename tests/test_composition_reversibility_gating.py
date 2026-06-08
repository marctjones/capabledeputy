"""Composition Sub-phase E — Reversibility gating in decide() (Demo #4).

The demo: agent burns through reversible/system + non-egressing
work autonomously. The same agent surfaces for approval the moment
the work becomes friction'd or non-system-reversal. Irreversible
work denies. Social.* effects always deny no matter what was
declared.

These tests pin the decide() composition. The legacy / v2 layers
still apply most-restrictively; the optimistic-auto carve-out only
relaxes when the v2 leg's default would otherwise have surfaced.
"""

from __future__ import annotations

from datetime import UTC, datetime

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import DecisionRules
from capabledeputy.policy.engine import (
    OPTIMISTIC_AUTO_RULE,
    REVERSIBILITY_IRREVERSIBLE_RULE,
    decide,
)
from capabledeputy.policy.labels import (
    AxisD,
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

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _scratch_cap() -> Capability:
    return Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="/scratch/*",
        origin=CapabilityOrigin.USER_APPROVED,
        allows_destructive=True,
    )


def _empty_label_state() -> tuple[LabelState, AxisD]:
    return (
        LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)})),
        AxisD(initiator="principal:alice"),
    )


def _r(degree: ReversibilityDegree, agent: ReversalAgent) -> ReversibilityLabel:
    return ReversibilityLabel(degree=degree, agent=agent)


# --- engine.decide() level ------------------------------------------


def test_reversible_system_non_egressing_optimistic_auto() -> None:
    """The headline case (Demo #4): reversible/system + non-egressing
    ⇒ AUTO without prompt. The v2 default would otherwise have been
    SUGGEST → REQUIRE_APPROVAL; the optimistic carve-out relaxes it
    to ALLOW."""
    labels, axis_d = _empty_label_state()
    result = decide(
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        labels=labels,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.REVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
    )
    assert result.decision == Decision.ALLOW
    assert result.rule == OPTIMISTIC_AUTO_RULE


def test_reversible_system_egressing_does_not_auto() -> None:
    """Same reversibility, but the effect class marks egress (e.g.,
    write_remote). Optimistic carve-out doesn't apply."""
    labels, axis_d = _empty_label_state()
    result = decide(
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        labels=labels,
        axis_d=axis_d,
        effect_class="data.write_remote",  # ← egressing
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.REVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
    )
    # Egress prevents optimistic auto; v2 default SUGGEST surfaces
    # as REQUIRE_APPROVAL.
    assert result.decision == Decision.REQUIRE_APPROVAL


def test_irreversible_effect_denies() -> None:
    """Irreversible effect ⇒ DENY regardless of capability holdings."""
    labels, axis_d = _empty_label_state()
    result = decide(
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        labels=labels,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.IRREVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
    )
    assert result.decision == Decision.DENY
    assert result.rule == REVERSIBILITY_IRREVERSIBLE_RULE


def test_reversible_human_requires_approval() -> None:
    """Reversal-agent=human ⇒ optimistic carve-out doesn't fire;
    the gate produces REQUIRE_APPROVAL. (The composing v2 default
    already ratchets to REQUIRE_APPROVAL too; either rule wins on
    a tie. What matters is the decision.)"""
    labels, axis_d = _empty_label_state()
    result = decide(
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        labels=labels,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.REVERSIBLE,
            ReversalAgent.HUMAN,
        ),
        now=_NOW,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL


def test_social_commitment_forced_irreversible_even_if_declared_reversible() -> None:
    """FR-019 hard rule: social.send_email is always treated irreversible —
    a reversible/system declaration DOES NOT win (it would otherwise
    AUTO-allow). Under the amended FR-019, irreversible COMMUNICATION egress
    routes to human APPROVAL (not the old hard DENY), so the gated outcome
    here is REQUIRE_APPROVAL — the point (declared-reversible didn't win) is
    preserved: it's gated, not auto-allowed."""
    labels, axis_d = _empty_label_state()
    matching_cap = Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        origin=CapabilityOrigin.USER_APPROVED,
        allows_destructive=True,
    )
    result = decide(
        frozenset({matching_cap}),
        Action(kind=CapabilityKind.WRITE_FS, target="x"),
        labels=labels,
        axis_d=axis_d,
        effect_class="social.send_email",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.REVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL


def test_legacy_only_path_also_applies_gate() -> None:
    """If the caller passes effective_reversibility but no v2 args,
    the reversibility gate still applies (back-compat path stays
    consistent)."""
    result = decide(
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        effect_class="data.write_scratch",
        effective_reversibility=_r(
            ReversibilityDegree.IRREVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
        labels=LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)})),
    )
    assert result.decision == Decision.DENY
    assert result.rule == REVERSIBILITY_IRREVERSIBLE_RULE


def test_no_reversibility_supplied_falls_to_v2_default() -> None:
    """Back-compat: without effective_reversibility, the gate is
    inert and the v2 default fires normally."""
    labels, axis_d = _empty_label_state()
    result = decide(
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        labels=labels,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        # no effective_reversibility
        now=_NOW,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL  # v2 default SUGGEST
