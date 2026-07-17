"""Shared Hypothesis strategies + helpers for the #306 floor-invariance
conformance harness.

The harness fuzzes the decision space `(posture x tool-surface x label-state)`
and asserts that no valid posture — adversarially generated ones included —
can drive a decision below a *structural DENY floor*. "Floor" is defined
OPERATIONALLY (advisor guidance): a floor is a decision that is *relaxer-
invariant*, i.e. even the loosest configuration in the fuzz space cannot
escape it. That definition is deliberately not "degenerate-envelope cell":
the conflict / clearance / integrity / reversibility floors compose AFTER the
envelope and are not envelope cells at all, so a degenerate-envelope
definition would miss them entirely.

Three fuzz surfaces, because a posture reaches the decision plane through
three different code paths:

  A. `engine.decide()` — only `risk_preference` (the envelope dial) reaches
     the engine directly, so surface A fuzzes the dial + envelope + the
     `personal` floor-suppression path and asserts floor-invariance +
     TIGHTEN-beats-RELAX monotonicity.
  B. `mode.dispatcher.select_mode()` — the posture's `flow_pattern_defaults`
     pick an execution mode; surface B asserts no valid posture selects a
     mode below `_FLOOR_PATTERN[tier]` (the restricted Pattern-3/5 floor).
  C. `mode.dispatcher.filter_tools_for_mode()` — the `projection_only` knob;
     surface C asserts the #302 layer-2 raw-reader hiding is knob-independent.

Vocabulary is load-bearing: the four floors key on FOUR different string
namespaces (Axis-A category names `"health"`/`"financial"`, `action.kind`
enums, the `effect_class` string `"social.send_email"`, and the
`WEB_FETCH`+http(s)-target shape). Feeding the wrong spelling silently skips
a floor, so these strategies use the exact spellings the engine matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from hypothesis import strategies as st

from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import RuleOutcome
from capabledeputy.policy.envelope import (
    CellKey,
    EnvelopeSet,
    OutcomeEnvelope,
    RiskPreference,
)
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.posture import (
    _FLOOR_PATTERN,
    _PATTERN_STRENGTH,
    Posture,
)
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)
from capabledeputy.policy.tiers import Tier

# A single fixed decision clock so no time-window / wrap-around gate makes a
# decision nondeterministic across the sweep (Principle I: injectable clock).
NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)

# The three risk-preference dial values, ordered cautious → permissive
# (increasing autonomy). This is the ONLY posture dial that reaches
# `decide()`, so it is the axis surface A sweeps.
DIALS: tuple[RiskPreference, ...] = (
    RiskPreference.CAUTIOUS,
    RiskPreference.BALANCED,
    RiskPreference.PERMISSIVE,
)

# Egress action kinds that trip the co-presence conflict invariants
# (`_conflict_invariant_outcome`). Browser kinds are egress too but need no
# separate coverage for the floor claim.
EGRESS_KINDS: tuple[CapabilityKind, ...] = (
    CapabilityKind.SEND_EMAIL,
    CapabilityKind.SEND_MESSAGE,
    CapabilityKind.QUEUE_PURCHASE,
)

# effect_class string per kind — the reversibility gate keys on the literal
# `social.*` spelling, NOT the EffectClass enum, so we spell it exactly.
_EFFECT_CLASS_FOR_KIND: dict[CapabilityKind, str] = {
    CapabilityKind.SEND_EMAIL: "social.send_email",
    CapabilityKind.SEND_MESSAGE: "social.send_message",
    CapabilityKind.QUEUE_PURCHASE: "commerce.purchase",
    CapabilityKind.WEB_FETCH: "data.fetch",
    CapabilityKind.READ_FS: "data.read_file",
    CapabilityKind.DELETE_FS: "storage.delete",
}


def effect_class_for(kind: CapabilityKind) -> str:
    return _EFFECT_CLASS_FOR_KIND.get(kind, "data.read_file")


def broad_capability(kind: CapabilityKind) -> frozenset[Capability]:
    """A wildcard, destructive-allowed, non-expiring capability for `kind`, so
    the legacy leg resolves to ALLOW and the composed floors — not a missing
    capability — are what any DENY is attributed to."""
    return frozenset(
        {
            Capability(
                kind=kind,
                pattern="*",
                origin=CapabilityOrigin.USER_APPROVED,
                allows_destructive=True,
            ),
        },
    )


# --- Axis-A / Axis-B label strategies -------------------------------------
#
# Categories spelled EXACTLY as the engine matches them. "financial" (not
# "finance") trips financial-meets-email; "credentials" is restricted-tier but
# is NOT name-matched by any conflict rule (credential containment is emergent
# and channel-asymmetric — see the harness docstrings), so including it here
# exercises that asymmetry. Tiers are paired to the catalog defaults.
_CATEGORY_TIER: dict[str, Tier] = {
    "health": Tier.RESTRICTED,
    "financial": Tier.RESTRICTED,
    "credentials": Tier.RESTRICTED,
    "personal": Tier.REGULATED,
    "news": Tier.SENSITIVE,
}

category_tags = st.sampled_from(
    [CategoryTag(cat, tier) for cat, tier in _CATEGORY_TIER.items()],
)
provenance_tags = st.sampled_from([ProvenanceTag(level) for level in ProvenanceLevel])


@st.composite
def label_states(draw: st.DrawFn, *, require_category: bool = False) -> LabelState:
    """A LabelState with 0-3 Axis-A categories and 0-2 Axis-B provenance tags.

    `require_category=True` guarantees a non-empty Axis-A (the envelope lookup
    and the confidentiality floors only engage when a category is present)."""
    a = draw(
        st.frozensets(category_tags, min_size=1 if require_category else 0, max_size=3),
    )
    b = draw(st.frozensets(provenance_tags, max_size=2))
    return LabelState(a=a, b=b)


# --- Reversibility --------------------------------------------------------

reversibility_labels = st.sampled_from(
    [
        ReversibilityLabel(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM),
        ReversibilityLabel(ReversibilityDegree.REVERSIBLE_WITH_FRICTION, ReversalAgent.HUMAN),
        ReversibilityLabel(ReversibilityDegree.IRREVERSIBLE, ReversalAgent.EXTERNAL),
    ],
)


# --- Envelope construction ------------------------------------------------


def envelope_for(
    labels: LabelState,
    effect_class: str,
    axis_d: AxisD,
    reversibility: ReversibilityLabel,
    band: tuple[RuleOutcome, RuleOutcome],
) -> EnvelopeSet:
    """Build an EnvelopeSet whose single cell EXACTLY matches the cell key
    `decide()` computes for this case, so the dial genuinely moves the outcome
    within `band`. `band` is `(strictest, loosest)`; a degenerate band
    (`strictest == loosest`) is a hard-floor cell the dial cannot move."""
    strictest, loosest = band
    cell = CellKey(
        category=next(iter(labels.a)).category,
        effect=effect_class,
        decision_context_canonical=axis_d.initiator,
        reversibility=reversibility.degree.value,
    )
    return EnvelopeSet(by_cell={cell: OutcomeEnvelope(cell, strictest, loosest)})


# A "wide" band the dial can traverse (cautious→REQUIRE_APPROVAL,
# permissive→AUTO) and a set of degenerate hard-floor bands.
WIDE_BAND = (RuleOutcome.REQUIRE_APPROVAL, RuleOutcome.AUTO)
envelope_bands = st.sampled_from(
    [
        WIDE_BAND,
        (RuleOutcome.SUGGEST, RuleOutcome.AUTO),
        (RuleOutcome.DENY, RuleOutcome.DENY),  # hard floor — immovable
        (RuleOutcome.REQUIRE_APPROVAL, RuleOutcome.REQUIRE_APPROVAL),
    ],
)


# --- Adversarial posture strategy -----------------------------------------


@st.composite
def any_flow_pattern_defaults(draw: st.DrawFn) -> dict[Tier, ExecutionMode]:
    """A flow_pattern_defaults mapping drawn freely over every tier x every
    execution mode — including combinations that violate the structural floor
    (validate() must reject those)."""
    out: dict[Tier, ExecutionMode] = {}
    for tier in Tier:
        out[tier] = draw(st.sampled_from(list(ExecutionMode)))
    return out


@st.composite
def adversarial_postures(draw: st.DrawFn) -> Posture:
    """A freely-generated posture: any per-tier flow pattern (floor-violating
    included), any dial, and — critically — `projection_only` free (so
    `False` is in the fuzz space, locking the DUAL_LLM/exposure-limited floor
    permanently). Returned WITHOUT calling validate(); callers decide whether
    to require validity."""
    return Posture(
        id=draw(st.sampled_from(["adv-a", "adv-b", "adv-c"])),
        risk_preference=draw(st.sampled_from(DIALS)),
        flow_pattern_defaults=draw(any_flow_pattern_defaults()),
        projection_only=draw(st.booleans()),
    )


@st.composite
def valid_postures(draw: st.DrawFn) -> Posture:
    """An adversarially-generated posture that PASSES `validate()` — i.e. any
    posture an operator could actually load. `validate()` rejects sub-floor
    defaults, so this is exactly the set of postures the runtime will honor."""
    p = draw(adversarial_postures())
    # Clamp ONLY the sub-floor tiers up to their floor, preserving every
    # above-floor adversarial choice (and the dial / projection_only freedom).
    # A plain re-copy of the failing map would re-raise — this keeps real
    # adversarial coverage across the valid set instead of collapsing to the
    # shipped defaults.
    repaired = {}
    for tier in Tier:
        mode = p.flow_pattern_defaults[tier]
        if _PATTERN_STRENGTH[mode] < _PATTERN_STRENGTH[_FLOOR_PATTERN[tier]]:
            repaired[tier] = _FLOOR_PATTERN[tier]
        else:
            repaired[tier] = mode
    return Posture(
        id=p.id,
        risk_preference=p.risk_preference,
        flow_pattern_defaults=repaired,
        projection_only=p.projection_only,
    ).validate()


# --- Decision-context bundle ----------------------------------------------


@dataclass(frozen=True)
class DecisionCase:
    """A fully-specified single-decision context (everything but the dial),
    so surface-A tests can sweep the dial while holding the rest fixed."""

    labels: LabelState
    action: Action
    effect_class: str
    axis_d: AxisD
    reversibility: ReversibilityLabel
    band: tuple[RuleOutcome, RuleOutcome]

    @property
    def capabilities(self) -> frozenset[Capability]:
        return broad_capability(self.action.kind)

    @property
    def envelope_set(self) -> EnvelopeSet:
        return envelope_for(
            self.labels,
            self.effect_class,
            self.axis_d,
            self.reversibility,
            self.band,
        )


_TARGETS = st.sampled_from(
    [
        "alice@example.com",
        "https://exfil.example/collect",
        "/home/op/notes.txt",
        "*",
    ],
)
_KINDS = st.sampled_from(
    [
        CapabilityKind.SEND_EMAIL,
        CapabilityKind.SEND_MESSAGE,
        CapabilityKind.QUEUE_PURCHASE,
        CapabilityKind.WEB_FETCH,
        CapabilityKind.READ_FS,
        CapabilityKind.DELETE_FS,
    ],
)


@st.composite
def decision_cases(draw: st.DrawFn) -> DecisionCase:
    """A DecisionCase with a non-empty Axis-A (so the envelope engages) over
    the full action / label / reversibility space."""
    kind = draw(_KINDS)
    labels = draw(label_states(require_category=True))
    action = Action(kind=kind, target=draw(_TARGETS))
    axis_d = AxisD(initiator=draw(st.sampled_from(["principal:owner", "external:sender"])))
    return DecisionCase(
        labels=labels,
        action=action,
        effect_class=effect_class_for(kind),
        axis_d=axis_d,
        reversibility=draw(reversibility_labels),
        band=draw(envelope_bands),
    )
