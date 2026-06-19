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
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier

_CAPS: frozenset[Capability] = frozenset(
    {
        Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*"),
        Capability(kind=CapabilityKind.BROWSER_AUTOMATION, pattern="*"),
        Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=10_000),
    },
)


def _action(kind: CapabilityKind) -> Action:
    if kind == CapabilityKind.QUEUE_PURCHASE:
        return Action(kind=kind, target="amazon", amount=50)
    if kind == CapabilityKind.BROWSER_AUTOMATION:
        return Action(kind=kind, target="https://example.com")
    return Action(kind=kind, target="alice@example.com")


def _untrusted() -> LabelState:
    return LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)}))


def _category(name: str) -> LabelState:
    return LabelState(a=frozenset({CategoryTag(category=name, tier=Tier.REGULATED)}))


# (four-axis LabelState, action kind, expected, rule id)
_SCENARIOS = [
    (
        _untrusted(),
        CapabilityKind.SEND_EMAIL,
        Decision.DENY,
        "untrusted-meets-egress",
    ),
    (
        _untrusted(),
        CapabilityKind.QUEUE_PURCHASE,
        Decision.DENY,
        "untrusted-meets-egress",
    ),
    (
        _untrusted(),
        CapabilityKind.BROWSER_AUTOMATION,
        Decision.DENY,
        "untrusted-meets-egress",
    ),
    (
        _category("health"),
        CapabilityKind.SEND_EMAIL,
        Decision.DENY,
        "health-meets-egress",
    ),
    (
        _category("health"),
        CapabilityKind.QUEUE_PURCHASE,
        Decision.DENY,
        "health-meets-egress",
    ),
    (
        _category("health"),
        CapabilityKind.BROWSER_AUTOMATION,
        Decision.DENY,
        "health-meets-egress",
    ),
    (
        _category("financial"),
        CapabilityKind.SEND_EMAIL,
        Decision.DENY,
        "financial-meets-email",
    ),
    (
        _category("financial"),
        CapabilityKind.BROWSER_AUTOMATION,
        Decision.DENY,
        "financial-meets-email",
    ),
    (
        _category("financial"),
        CapabilityKind.QUEUE_PURCHASE,
        Decision.REQUIRE_APPROVAL,
        "financial-meets-purchase",
    ),
]


@pytest.mark.parametrize(("labels", "kind", "expected", "rule"), _SCENARIOS)
def test_four_axis_gate_matches_expected(
    labels: LabelState,
    kind: CapabilityKind,
    expected: Decision,
    rule: str,
) -> None:
    """The four-axis gate, driven by `labels=` with an empty flat
    `label_set`, produces the documented outcome + rule id."""
    result = decide(
        _CAPS,
        _action(kind),
        labels=labels,
    )
    assert result.decision == expected
    assert result.rule == rule


@pytest.mark.parametrize(("labels", "kind", "expected", "rule"), _SCENARIOS)
def test_four_axis_gate_consistent(
    labels: LabelState,
    kind: CapabilityKind,
    expected: Decision,
    rule: str,
) -> None:
    """The four-axis gate consistently yields the expected decision +
    rule id across multiple invocations."""
    result1 = decide(_CAPS, _action(kind), labels=labels)
    result2 = decide(_CAPS, _action(kind), labels=labels)
    assert result1.decision == result2.decision == expected
    assert result1.rule == result2.rule == rule


def test_no_conflict_when_axes_benign() -> None:
    """Principal-direct provenance + no sensitive category ⇒ no
    invariant fires; the egress action is allowed."""
    benign = LabelState(
        a=frozenset({CategoryTag(category="personal", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    result = decide(
        _CAPS,
        _action(CapabilityKind.SEND_EMAIL),
        labels=benign,
    )
    assert result.decision == Decision.ALLOW


def test_invariant_is_always_on_without_v2_wiring() -> None:
    """No rules_v2 / effect_class / envelope supplied — the invariant
    still fires from the axes alone."""
    result = decide(
        _CAPS,
        _action(CapabilityKind.SEND_EMAIL),
        labels=_untrusted(),
    )
    assert result.decision == Decision.DENY
    assert result.rule == "untrusted-meets-egress"


def test_non_egress_action_is_unaffected() -> None:
    """The invariants gate only egress kinds; a read with the same taint
    is not denied here."""
    result = decide(
        frozenset({Capability(kind=CapabilityKind.READ_FS, pattern="*")}),
        Action(kind=CapabilityKind.READ_FS, target="/x"),
        labels=_untrusted(),
    )
    assert result.decision == Decision.ALLOW
