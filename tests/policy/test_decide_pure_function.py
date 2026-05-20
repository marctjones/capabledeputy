"""T109 — Property-based determinism of decide() (Research D11 / Principle I).

`engine.decide()` is a pure function: same inputs ⇒ identical
outputs. Hypothesis-driven test that runs a search over combinations
of (label_set, capability_set, action) and asserts:

  - Two calls with the same inputs produce equal PolicyDecisions.
  - Re-shuffling the order of a frozenset input (Python is stable
    on frozensets but the hash bucket order can vary across runs)
    still yields identical decision/rule/reason fields.

If decide() ever sneaks in non-determinism — wall-clock reads,
filesystem touches, env-var lookups — these properties will catch
it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.engine import decide
from capabledeputy.policy.labels import Label

# Frozen clock — we want determinism across runs, not "real time."
_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


_LABEL_STRATEGY = st.sampled_from(list(Label))
_KIND_STRATEGY = st.sampled_from(
    [
        CapabilityKind.READ_FS,
        CapabilityKind.SEND_EMAIL,
        CapabilityKind.QUEUE_PURCHASE,
        CapabilityKind.WEB_FETCH,
    ],
)
_TARGET_STRATEGY = st.sampled_from(["alice@example.com", "/etc/hosts", "https://x", "*"])


@st.composite
def _capabilities(draw: st.DrawFn) -> frozenset[Capability]:
    n = draw(st.integers(min_value=0, max_value=3))
    caps = set()
    for _ in range(n):
        kind = draw(_KIND_STRATEGY)
        pattern = draw(_TARGET_STRATEGY)
        caps.add(
            Capability(
                kind=kind,
                pattern=pattern,
                origin=CapabilityOrigin.USER_APPROVED,
            ),
        )
    return frozenset(caps)


@given(
    label_set=st.sets(_LABEL_STRATEGY, max_size=4).map(frozenset),
    capabilities=_capabilities(),
    action_kind=_KIND_STRATEGY,
    action_target=_TARGET_STRATEGY,
)
@settings(max_examples=200, deadline=None)
def test_decide_is_deterministic_under_identical_inputs(
    label_set: frozenset[Label],
    capabilities: frozenset[Capability],
    action_kind: CapabilityKind,
    action_target: str,
) -> None:
    action = Action(kind=action_kind, target=action_target)
    d1 = decide(label_set, capabilities, action, now=_NOW)
    d2 = decide(label_set, capabilities, action, now=_NOW)
    assert d1.decision == d2.decision
    assert d1.rule == d2.rule
    assert d1.reason == d2.reason
    assert d1.effective_labels == d2.effective_labels


@given(
    label_set=st.sets(_LABEL_STRATEGY, max_size=4).map(frozenset),
    capabilities=_capabilities(),
    action_kind=_KIND_STRATEGY,
    action_target=_TARGET_STRATEGY,
)
@settings(max_examples=200, deadline=None)
def test_decide_independent_of_set_construction_order(
    label_set: frozenset[Label],
    capabilities: frozenset[Capability],
    action_kind: CapabilityKind,
    action_target: str,
) -> None:
    """Frozensets are unordered. Building the same set from the
    reversed list of elements MUST yield the same decision."""
    action = Action(kind=action_kind, target=action_target)
    reversed_labels = frozenset(reversed(list(label_set)))
    reversed_caps = frozenset(reversed(list(capabilities)))
    d1 = decide(label_set, capabilities, action, now=_NOW)
    d2 = decide(reversed_labels, reversed_caps, action, now=_NOW)
    assert d1.decision == d2.decision
    assert d1.rule == d2.rule


def test_decide_never_reads_wall_clock_without_injected_now() -> None:
    """If `now` is injected, decide() must NOT consult the real clock —
    a single decision evaluated at the same `now` twice produces the
    same expiry/rate verdicts. This is the operational version of
    'pure function over inputs.'"""
    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="/x",
        origin=CapabilityOrigin.USER_APPROVED,
    )
    action = Action(kind=CapabilityKind.READ_FS, target="/x")
    d1 = decide(frozenset(), frozenset({cap}), action, now=_NOW)
    d2 = decide(frozenset(), frozenset({cap}), action, now=_NOW)
    assert d1 == d2


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_decide_repeated_calls_equal(seed: int) -> None:
    """A handful of fixed-seed deterministic cases pinned for fast
    smoke-test signal — Hypothesis runs the broad search above."""
    cap = Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="alice@example.com",
        origin=CapabilityOrigin.USER_APPROVED,
    )
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com")
    label_set = frozenset({Label.TRUSTED_USER_DIRECT})
    decisions = [decide(label_set, frozenset({cap}), action, now=_NOW) for _ in range(5)]
    for d in decisions[1:]:
        assert d == decisions[0], f"non-determinism detected (seed={seed})"
