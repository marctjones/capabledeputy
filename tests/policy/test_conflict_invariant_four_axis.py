"""R4c — the four-axis information-flow conflict invariants
(`_conflict_invariant_outcome`) reproduce the flat `CONFLICT_RULES`
outcomes off the propagating axes (decision D-conflict,
label-model-redesign §R4c).

Two guarantees are proven here:

1. **Run-both-and-assert-agreement.** For every canonical scenario the
   four-axis path (empty `label_set`, equivalent `labels=LabelState`)
   yields the *same* decision + rule id as the legacy flat path
   (`label_set=`, no axes). This is the safety net that lets R4d delete
   the flat leg without changing outcomes.
2. **Always-on.** The invariants fire from the axes alone — no
   `rules_v2`, envelope, or other v2 wiring required — exactly like the
   flat `CONFLICT_RULES` they replace.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.engine import decide
from capabledeputy.policy.labels import (
    CategoryTag,
    Label,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier

_CAPS: frozenset[Capability] = frozenset(
    {
        Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*"),
        Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=10_000),
    },
)


def _action(kind: CapabilityKind) -> Action:
    if kind == CapabilityKind.QUEUE_PURCHASE:
        return Action(kind=kind, target="amazon", amount=50)
    return Action(kind=kind, target="alice@example.com")


def _untrusted() -> LabelState:
    return LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)}))


def _category(name: str) -> LabelState:
    return LabelState(a=frozenset({CategoryTag(category=name, tier=Tier.REGULATED)}))


# (flat label_set, four-axis LabelState, action kind, expected, rule id)
_SCENARIOS = [
    (
        frozenset({Label.UNTRUSTED_EXTERNAL}),
        _untrusted(),
        CapabilityKind.SEND_EMAIL,
        Decision.DENY,
        "untrusted-meets-egress",
    ),
    (
        frozenset({Label.UNTRUSTED_EXTERNAL}),
        _untrusted(),
        CapabilityKind.QUEUE_PURCHASE,
        Decision.DENY,
        "untrusted-meets-egress",
    ),
    (
        frozenset({Label.CONFIDENTIAL_HEALTH}),
        _category("health"),
        CapabilityKind.SEND_EMAIL,
        Decision.DENY,
        "health-meets-egress",
    ),
    (
        frozenset({Label.CONFIDENTIAL_HEALTH}),
        _category("health"),
        CapabilityKind.QUEUE_PURCHASE,
        Decision.DENY,
        "health-meets-egress",
    ),
    (
        frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        _category("financial"),
        CapabilityKind.SEND_EMAIL,
        Decision.DENY,
        "financial-meets-email",
    ),
    (
        frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        _category("financial"),
        CapabilityKind.QUEUE_PURCHASE,
        Decision.REQUIRE_APPROVAL,
        "financial-meets-purchase",
    ),
]


@pytest.mark.parametrize(("flat", "labels", "kind", "expected", "rule"), _SCENARIOS)
def test_four_axis_gate_matches_expected(
    flat: frozenset[Label],
    labels: LabelState,
    kind: CapabilityKind,
    expected: Decision,
    rule: str,
) -> None:
    """The four-axis gate, driven by `labels=` with an empty flat
    `label_set`, produces the documented outcome + rule id."""
    result = decide(
        frozenset(),
        _CAPS,
        _action(kind),
        labels=labels,
    )
    assert result.decision == expected
    assert result.rule == rule


@pytest.mark.parametrize(("flat", "labels", "kind", "expected", "rule"), _SCENARIOS)
def test_four_axis_agrees_with_flat_leg(
    flat: frozenset[Label],
    labels: LabelState,
    kind: CapabilityKind,
    expected: Decision,
    rule: str,
) -> None:
    """Run-both-and-assert-agreement: the legacy flat leg (`label_set`,
    no axes) and the four-axis leg (`labels=`, empty `label_set`) reach
    the identical decision + rule for every scenario."""
    flat_result = decide(flat, _CAPS, _action(kind))
    axis_result = decide(frozenset(), _CAPS, _action(kind), labels=labels)
    assert flat_result.decision == axis_result.decision == expected
    assert flat_result.rule == axis_result.rule == rule


def test_no_conflict_when_axes_benign() -> None:
    """Principal-direct provenance + no sensitive category ⇒ no
    invariant fires; the egress action is allowed."""
    benign = LabelState(
        a=frozenset({CategoryTag(category="personal", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    result = decide(
        frozenset(),
        _CAPS,
        _action(CapabilityKind.SEND_EMAIL),
        labels=benign,
    )
    assert result.decision == Decision.ALLOW


def test_invariant_is_always_on_without_v2_wiring() -> None:
    """No rules_v2 / effect_class / envelope supplied — the invariant
    still fires from the axes alone, like the flat CONFLICT_RULES."""
    result = decide(
        frozenset(),
        _CAPS,
        _action(CapabilityKind.SEND_EMAIL),
        labels=_untrusted(),
    )
    assert result.decision == Decision.DENY
    assert result.rule == "untrusted-meets-egress"


def test_non_egress_action_is_unaffected() -> None:
    """The invariants gate only egress kinds (SEND_EMAIL /
    QUEUE_PURCHASE); a read with the same taint is not denied here."""
    result = decide(
        frozenset(),
        frozenset({Capability(kind=CapabilityKind.READ_FS, pattern="*")}),
        Action(kind=CapabilityKind.READ_FS, target="/x"),
        labels=_untrusted(),
    )
    assert result.decision == Decision.ALLOW
