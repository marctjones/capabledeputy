"""#306 surface A — `engine.decide()` floor-invariance under the posture dial.

The only posture input that reaches `decide()` is `risk_preference` (the
envelope dial). So surface A fuzzes the dial (plus the envelope band and the
`personal` floor-suppression path) and asserts:

  A1  The untrusted→egress floor is IMMOVABLE — no dial value, no envelope
      band, and not even a `personal`-trust-profile human-ratified rule that
      explicitly names it can turn it from DENY. This is the one floor that is
      never rule-crossable (engine.py `_compose_with_conflict_invariant` line
      ~462 excludes it; only an Override Grant crosses it, which the harness
      excludes with `override_grants=None`).
  A2  Dial monotonicity (TIGHTEN-beats-RELAX, end-to-end): as the dial moves
      cautious → balanced → permissive the composed decision is monotone
      NON-INCREASING in restrictiveness. Relaxing the dial can never tighten
      the outcome, and — the point — can never loosen it PAST a floor.
  A3  Floor-preservation (operational floor definition): if even the loosest
      dial (permissive) yields a floor (DENY / OVERRIDE_REQUIRED), then every
      stricter dial yields a floor too. A floor is by definition relaxer-
      invariant.
  A4  Cross-preset floor equality: the three shipped presets, which at the
      decide() layer differ ONLY by their `risk_preference`, produce
      byte-identical decisions on every floor cell (and never straddle
      DENY↔non-DENY off a floor cell).

TLA mapping (honest — the harness checks a SUPERSET of the model):
  * A1's health-category leg corresponds to `NoSilentEgressOnPHI` in
    `spec/CapableDeputy.tla` (no ALLOW when confidential.health co-occurs with
    an egress). The untrusted leg is the four-axis port of the same model's
    `untrusted-meets-egress` conflict rule.
  * Every decision produced here is one of the model's decision outcomes
    (`PolicyDecisionTotal`).
  * The dial / envelope / posture invariance (A2-A4) is BEYOND the TLA model —
    the `.tla` has no posture, dial, or envelope concept. Labeled as such so
    the harness never overclaims model-equivalence.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.decision_rules import (
    DecisionRule,
    DecisionRules,
    RuleOutcome,
    RulePredicate,
)
from capabledeputy.policy.engine import (
    _LEGACY_RANK,
    PROVENANCE_EGRESS_RULE,
    decide,
)
from capabledeputy.policy.envelope import RiskPreference
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.posture import BUILTIN_POSTURES
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from tests.invariants.floor_strategies import (
    DIALS,
    EGRESS_KINDS,
    NOW,
    WIDE_BAND,
    DecisionCase,
    broad_capability,
    decision_cases,
    effect_class_for,
    envelope_bands,
    envelope_for,
)

_SETTINGS = settings(
    max_examples=250,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Floor outcomes: DENY and OVERRIDE_REQUIRED are the two "structural floor"
# ranks — an operator dial can never dissolve either into ALLOW/WARN.
_FLOOR_DECISIONS = frozenset({Decision.DENY, Decision.OVERRIDE_REQUIRED})


def _decide_at(
    case,
    dial: RiskPreference,
    *,
    personal: bool = False,
    rules_v2: DecisionRules | None = None,
) -> Decision:
    """Run `decide()` for `case` at dial `dial`. Legacy-only path unless
    `rules_v2` is supplied (which also engages the personal-crossing path)."""
    kwargs: dict = {
        "labels": case.labels,
        "axis_d": case.axis_d,
        "effect_class": case.effect_class,
        "effective_reversibility": case.reversibility,
        "envelope_set": case.envelope_set,
        "risk_preference": dial,
        "override_grants": None,  # exclude the one universal floor-crosser
        "trust_profile_is_personal": personal,
    }
    if rules_v2 is not None:
        kwargs["rules_v2"] = rules_v2
    return decide(case.capabilities, case.action, now=NOW, **kwargs).decision


# --- A5 — absolute per-floor assertions -----------------------------------
#
# A2/A3/A4 are RELATIVE (monotonicity + "floor at permissive ⇒ floor
# everywhere"). Their weakness: a regression that lets even the LOOSEST dial
# escape a floor turns A3/A4's precondition False (they skip) while A2 stays
# trivially monotone (4≤4≤4). A mutation test confirmed this — neutering
# health-meets-egress left A2/A3/A4 green. So each named floor also needs an
# ABSOLUTE assertion: the exact outcome, dial-independent, never loosened by any
# envelope band. Channel-asymmetry is load-bearing (per the engine map): health
# denies every egress channel, financial denies email/message but only
# REQUIRE_APPROVALs purchase, and credential containment on email is NOT a
# decide()-level DENY (it is REQUIRE_APPROVAL + structural containment on
# surfaces B/C) so it is deliberately absent here.

_IRREVERSIBLE = ReversibilityLabel(ReversibilityDegree.IRREVERSIBLE, ReversalAgent.EXTERNAL)
_CATEGORY_TIER = {
    "health": Tier.RESTRICTED,
    "financial": Tier.RESTRICTED,
    "credentials": Tier.RESTRICTED,
    "personal": Tier.REGULATED,
}

# (id, category, action.kind, target, reversibility, expected floor outcome)
_FLOOR_CELLS = [
    ("health-email", "health", CapabilityKind.SEND_EMAIL, "a@b.com", None, Decision.DENY),
    ("health-message", "health", CapabilityKind.SEND_MESSAGE, "a@b.com", None, Decision.DENY),
    ("health-purchase", "health", CapabilityKind.QUEUE_PURCHASE, "sku-1", None, Decision.DENY),
    ("financial-email", "financial", CapabilityKind.SEND_EMAIL, "a@b.com", None, Decision.DENY),
    ("financial-message", "financial", CapabilityKind.SEND_MESSAGE, "a@b.com", None, Decision.DENY),
    # Channel-asymmetry: financial + purchase is REQUIRE_APPROVAL, not DENY.
    (
        "financial-purchase",
        "financial",
        CapabilityKind.QUEUE_PURCHASE,
        "sku-1",
        None,
        Decision.REQUIRE_APPROVAL,
    ),
    # fetch-url-restricted: restricted-tier category + WEB_FETCH to a
    # non-allowlisted http(s) URL is the exfil channel — hard DENY.
    (
        "fetch-restricted",
        "credentials",
        CapabilityKind.WEB_FETCH,
        "https://exfil.example/collect",
        None,
        Decision.DENY,
    ),
    # irreversible non-communication effect (delete) — hard DENY.
    (
        "irreversible-delete",
        "personal",
        CapabilityKind.DELETE_FS,
        "/home/op/keep.txt",
        _IRREVERSIBLE,
        Decision.DENY,
    ),
]

# Bands to sweep: None (dial-neutral) plus every envelope band, so no dial and
# no envelope can loosen the floor below its expected outcome.
_BANDS = [None, WIDE_BAND, (RuleOutcome.DENY, RuleOutcome.DENY)]


@pytest.mark.parametrize(
    ("cell_id", "category", "kind", "target", "reversibility", "expected"),
    _FLOOR_CELLS,
    ids=[c[0] for c in _FLOOR_CELLS],
)
def test_named_floor_holds_absolutely(
    cell_id: str,
    category: str,
    kind: CapabilityKind,
    target: str,
    reversibility,
    expected: Decision,
) -> None:
    """A5 — each named structural floor produces its exact outcome across every
    dial and every envelope band (personal=False, no override grant). Dial-
    neutral (no envelope) it equals the floor exactly; with any band it is never
    LOOSER than the floor. This nails each floor to the wall — a regression that
    dropped it to ALLOW/WARN would fail here even though A2/A3/A4 would not."""
    labels = LabelState(a=frozenset({CategoryTag(category, _CATEGORY_TIER[category])}))
    action = Action(kind=kind, target=target)
    effect_class = effect_class_for(kind)
    axis_d = AxisD(initiator="principal:owner")
    for dial in DIALS:
        for band in _BANDS:
            envelope_set = (
                envelope_for(labels, effect_class, axis_d, reversibility or _IRREVERSIBLE, band)
                if band is not None
                else None
            )
            result = decide(
                broad_capability(kind),
                action,
                now=NOW,
                labels=labels,
                axis_d=axis_d,
                effect_class=effect_class,
                effective_reversibility=reversibility,
                envelope_set=envelope_set,
                risk_preference=dial,
                override_grants=None,
            )
            if band is None:
                assert result.decision == expected, (
                    f"{cell_id}: dial={dial} band=None -> {result.decision} "
                    f"(rule={result.rule}), expected {expected}"
                )
            else:
                # An envelope band may only TIGHTEN below the floor, never loosen
                # it. _LEGACY_RANK: lower = stricter.
                assert _LEGACY_RANK[result.decision] <= _LEGACY_RANK[expected], (
                    f"{cell_id}: dial={dial} band={band} loosened floor to "
                    f"{result.decision} (rule={result.rule})"
                )


# --- A1 — the untrusted→egress floor is immovable -------------------------


def _untrusted_crossing_rule() -> DecisionRules:
    """A human-RATIFIED rule that (illegitimately) names the untrusted floor
    in `crosses_floor`, built directly to bypass the load-time refusal — the
    exact defense-in-depth case: the engine must STILL not cross it."""
    return DecisionRules(
        rules=(
            DecisionRule(
                rule_id="adversary-cross-untrusted",
                predicate=RulePredicate(
                    axis_b_provenance="external-untrusted",
                    effect_class=None,
                ),
                outcome=RuleOutcome.AUTO,
                rationale="adversarial: try to auto-cross untrusted floor",
                human_ratified_by="owner",
                crosses_floor="untrusted-meets-egress",
            ),
        ),
    )


@given(
    kind=st.sampled_from(EGRESS_KINDS),
    target=st.sampled_from(["alice@example.com", "https://exfil.example/x", "*"]),
    dial=st.sampled_from(DIALS),
    personal=st.booleans(),
    with_crossing_rule=st.booleans(),
    band=envelope_bands,
    extra_category=st.sampled_from([None, "health", "financial", "personal"]),
)
@_SETTINGS
def test_untrusted_egress_floor_is_immovable(
    kind: CapabilityKind,
    target: str,
    dial: RiskPreference,
    personal: bool,
    with_crossing_rule: bool,
    band,
    extra_category,
) -> None:
    """A1 — external-untrusted provenance + an egress action is DENY across the
    ENTIRE relaxer space: every dial, personal or managed, with or without a
    ratified rule that names the floor, any envelope band. Nothing crosses it
    but an Override Grant (excluded)."""
    a = frozenset()
    if extra_category is not None:
        tier = Tier.RESTRICTED if extra_category in {"health", "financial"} else Tier.REGULATED
        a = frozenset({CategoryTag(extra_category, tier)})
    labels = LabelState(
        a=a,
        b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
    )
    action = Action(kind=kind, target=target)
    effect_class = effect_class_for(kind)
    axis_d = AxisD(initiator="external:sender")
    reversibility = None
    envelope_set = None
    # Only build an envelope when there's a category to key it on.
    from capabledeputy.policy.reversibility import (
        ReversalAgent,
        ReversibilityDegree,
        ReversibilityLabel,
    )

    if a:
        reversibility = ReversibilityLabel(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM)
        envelope_set = envelope_for(labels, effect_class, axis_d, reversibility, band)

    rules_v2 = _untrusted_crossing_rule() if with_crossing_rule else None
    result = decide(
        broad_capability(kind),
        action,
        now=NOW,
        labels=labels,
        axis_d=axis_d,
        effect_class=effect_class,
        effective_reversibility=reversibility,
        envelope_set=envelope_set,
        risk_preference=dial,
        rules_v2=rules_v2,
        trust_profile_is_personal=personal,
        override_grants=None,
    )
    assert result.decision == Decision.DENY, (
        f"untrusted→egress must DENY; got {result.decision} (rule={result.rule}, "
        f"dial={dial}, personal={personal}, crossing_rule={with_crossing_rule})"
    )
    # In the isolated case (no co-present category ⇒ no envelope/other floor to
    # compose DENY first) the DENY is attributed to the untrusted floor by name.
    # When another floor (health/financial envelope) also yields DENY it may win
    # the rule-label race — the decision stays DENY either way, which is the
    # floor property; the label is incidental.
    if extra_category is None:
        assert result.rule == PROVENANCE_EGRESS_RULE


# --- A2 / A3 — dial monotonicity + floor preservation ---------------------


@given(case=decision_cases())
@_SETTINGS
def test_dial_monotone_and_floor_preserving(case) -> None:
    """A2 + A3 — sweeping the dial cautious → balanced → permissive, the
    decision is monotone non-increasing in restrictiveness, and a floor at the
    loosest dial is a floor at every dial."""
    d_cautious = _decide_at(case, RiskPreference.CAUTIOUS)
    d_balanced = _decide_at(case, RiskPreference.BALANCED)
    d_permissive = _decide_at(case, RiskPreference.PERMISSIVE)

    # A2 — monotonicity. _LEGACY_RANK: lower = stricter. Relaxing the dial
    # must never DECREASE the rank (never tighten).
    assert _LEGACY_RANK[d_cautious] <= _LEGACY_RANK[d_balanced] <= _LEGACY_RANK[d_permissive], (
        f"dial not monotone: cautious={d_cautious} balanced={d_balanced} permissive={d_permissive}"
    )

    # A3 — floor preservation. If the loosest dial cannot escape a floor, no
    # dial can. (Follows from A2 since DENY/OVERRIDE are the minimum ranks, but
    # asserted directly as the human-readable floor guarantee.)
    if d_permissive in _FLOOR_DECISIONS:
        assert d_cautious in _FLOOR_DECISIONS and d_balanced in _FLOOR_DECISIONS, (
            f"floor at permissive ({d_permissive}) but escaped at a stricter dial: "
            f"cautious={d_cautious} balanced={d_balanced}"
        )


# --- A4 — cross-preset floor equality -------------------------------------


@given(case=decision_cases())
@_SETTINGS
def test_shipped_presets_agree_on_floor_cells(case) -> None:
    """A4 — the three shipped presets differ at the decide() layer ONLY by
    their risk_preference dial, so on any floor cell (a cell that is a floor at
    the most-permissive preset) all three produce byte-identical decisions, and
    off a floor cell they never straddle the DENY↔non-DENY line."""
    by_preset = {
        pid: _decide_at(case, posture.risk_preference) for pid, posture in BUILTIN_POSTURES.items()
    }
    decisions = set(by_preset.values())

    # Identify floor cells operationally: the loosest shipped preset's dial.
    loosest = max(
        BUILTIN_POSTURES.values(),
        key=lambda p: {
            RiskPreference.CAUTIOUS: 0,
            RiskPreference.BALANCED: 1,
            RiskPreference.PERMISSIVE: 2,
        }[p.risk_preference],
    )
    loosest_decision = _decide_at(case, loosest.risk_preference)

    if loosest_decision in _FLOOR_DECISIONS:
        assert len(decisions) == 1, f"floor cell but presets disagree: {by_preset}"
    else:
        # Off a floor cell the presets may differ within the envelope band, but
        # must never straddle DENY↔non-DENY (a floor must not appear for one
        # preset and vanish for another).
        floored = {d in _FLOOR_DECISIONS for d in decisions}
        assert len(floored) == 1, f"presets straddle the floor line: {by_preset}"


# --- Non-vacuity witnesses -------------------------------------------------
#
# Deterministic anchors proving the fuzzed surfaces above actually EXERCISE a
# live floor / a moving dial, so a future refactor that made the properties
# pass vacuously (e.g. every case short-circuiting to the same decision) would
# still fail here.


def _clean_wide_case() -> DecisionCase:
    from capabledeputy.policy.reversibility import (
        ReversalAgent,
        ReversibilityDegree,
        ReversibilityLabel,
    )

    labels = LabelState(a=frozenset({CategoryTag("news", Tier.SENSITIVE)}))
    return DecisionCase(
        labels=labels,
        action=Action(kind=CapabilityKind.READ_FS, target="/home/op/n.txt"),
        effect_class="data.read_file",
        axis_d=AxisD(initiator="principal:owner"),
        reversibility=ReversibilityLabel(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM),
        band=WIDE_BAND,
    )


def test_witness_dial_actually_moves_an_outcome() -> None:
    """The envelope dial genuinely spans REQUIRE_APPROVAL→ALLOW on a wide,
    floor-free cell — so the monotonicity property is non-vacuous."""
    case = _clean_wide_case()
    d_cautious = _decide_at(case, RiskPreference.CAUTIOUS)
    d_permissive = _decide_at(case, RiskPreference.PERMISSIVE)
    assert d_cautious == Decision.REQUIRE_APPROVAL
    assert d_permissive == Decision.ALLOW
    assert _LEGACY_RANK[d_cautious] < _LEGACY_RANK[d_permissive]


def test_witness_untrusted_email_denies_by_name() -> None:
    """The isolated untrusted→email case denies and is attributed to the
    untrusted floor by name — the headline A1 floor, pinned concretely."""
    result = decide(
        broad_capability(CapabilityKind.SEND_EMAIL),
        Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com"),
        now=NOW,
        labels=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
        override_grants=None,
    )
    assert result.decision == Decision.DENY
    assert result.rule == PROVENANCE_EGRESS_RULE
