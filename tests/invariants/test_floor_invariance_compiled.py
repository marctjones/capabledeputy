"""#389 — the #306 floor guarantees hold for policy AUTHORED in the unified
grammar and compiled through `policy/authoring.py`.

This is what makes the whole authoring migration safe: no matter what an operator
writes in the compact `when → outcome` surface, the compiled output cannot drive
a decision below a structural floor. It ties the authoring layer (Phase 1/2) to
the #306 conformance guarantees, and it is the standing net Phase-2/3 changes run
behind.

Two properties:
  1. An authored rule that tries to ALLOW a floor-triggering action, compiled and
     fed to the real engine, cannot cross the floor — the untrusted floor is
     never crossable, and other floors are not crossable without an explicit
     ratified personal-crossing.
  2. Any posture authored via the grammar that VALIDATES respects the mode floor
     (restricted ⇒ REFERENCE/SEALED or fail-closed) — and a sub-floor authored
     posture is rejected at compile time.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from capabledeputy.mode.dispatcher import (
    ExecutionMode,
    ModeSelectionError,
    select_mode,
)
from capabledeputy.policy.actions import Action
from capabledeputy.policy.authoring import ConfigError, compile_posture, compile_rules
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import PROVENANCE_EGRESS_RULE, decide
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.posture import _FLOOR_PATTERN, _PATTERN_STRENGTH
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.registry import ToolRegistry
from tests.invariants.floor_strategies import NOW, broad_capability, effect_class_for

_SETTINGS = settings(max_examples=200, deadline=None)

_EGRESS = ["send_email", "send_message", "queue_purchase"]
_KIND_OF = {
    "send_email": CapabilityKind.SEND_EMAIL,
    "send_message": CapabilityKind.SEND_MESSAGE,
    "queue_purchase": CapabilityKind.QUEUE_PURCHASE,
}


@given(
    action_word=st.sampled_from(_EGRESS),
    then_word=st.sampled_from(["allow", "auto", "suggest", "approve"]),
    ratified=st.booleans(),
    crosses=st.sampled_from([None, "untrusted-meets-egress", "health-meets-egress"]),
    personal=st.booleans(),
)
@_SETTINGS
def test_authored_rule_cannot_cross_untrusted_floor(
    action_word: str,
    then_word: str,
    ratified: bool,
    crosses,
    personal: bool,
) -> None:
    """An operator rule authored to auto-allow an untrusted→egress action —
    however phrased, ratified or not, personal or not, even naming the floor in
    crosses_floor — compiles and STILL denies at the engine. The untrusted floor
    is never crossable."""
    entry = {"id": "adv", "when": f"external + {action_word}", "then": then_word}
    if ratified:
        entry["human_ratified_by"] = "owner"
    if crosses is not None:
        entry["crosses_floor"] = crosses
    rules = compile_rules([entry])

    kind = _KIND_OF[action_word]
    result = decide(
        broad_capability(kind),
        Action(kind=kind, target="bob@example.com"),
        now=NOW,
        labels=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
        axis_d=AxisD(initiator="external:sender"),
        effect_class=effect_class_for(kind),
        rules_v2=rules,
        trust_profile_is_personal=personal,
        override_grants=None,
    )
    assert result.decision == Decision.DENY
    assert result.rule == PROVENANCE_EGRESS_RULE


@given(
    action_word=st.sampled_from(_EGRESS),
    then_word=st.sampled_from(["allow", "auto"]),
)
@_SETTINGS
def test_authored_rule_cannot_cross_health_floor_without_crossing(
    action_word: str,
    then_word: str,
) -> None:
    """An authored `health + <egress> → allow` rule that does NOT explicitly name
    the health floor (and is not under a personal profile) cannot cross it."""
    rules = compile_rules(
        [
            {
                "id": "adv",
                "when": f"health + {action_word}",
                "then": then_word,
                "human_ratified_by": "owner",
            },
        ],
    )
    kind = _KIND_OF[action_word]
    result = decide(
        broad_capability(kind),
        Action(kind=kind, target="doc@example.com"),
        now=NOW,
        labels=LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)})),
        axis_d=AxisD(initiator="principal:owner"),
        effect_class=effect_class_for(kind),
        rules_v2=rules,
        trust_profile_is_personal=False,  # managed: floor re-applies
        override_grants=None,
    )
    assert result.decision == Decision.DENY


# --- authored postures via the grammar respect the mode floor -------------


@st.composite
def _posture_docs(draw: st.DrawFn) -> dict:
    modes = [m.value for m in ExecutionMode]
    tiers = [t.value for t in Tier]
    flow = {
        draw(st.sampled_from(tiers)): draw(st.sampled_from(modes))
        for _ in range(draw(st.integers(0, 3)))
    }
    return {
        "id": draw(st.sampled_from(["a", "b", "c"])),
        "dial": draw(st.sampled_from(["cautious", "balanced", "permissive"])),
        "projection_only": draw(st.booleans()),
        "flow_patterns": flow,
    }


@given(doc=_posture_docs())
@_SETTINGS
def test_authored_posture_respects_mode_floor_or_is_rejected(doc: dict) -> None:
    """A posture authored in the grammar either fails to compile (a sub-floor
    flow pattern is rejected) or, if it compiles, never selects a mode below the
    tier floor — including for restricted data."""
    try:
        posture = compile_posture(doc)
    except ConfigError:
        return  # sub-floor rejected at compile time — the floor held
    assert posture is not None

    restricted = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))
    try:
        mode, _ = select_mode(restricted, ToolRegistry(), posture=posture)
    except ModeSelectionError:
        return  # fail-closed honors the floor
    assert mode not in {
        ExecutionMode.TURN_LEVEL,
        ExecutionMode.DUAL_LLM,
        ExecutionMode.PROGRAMMATIC,
    }
    # And no per-tier default is below its floor.
    for tier in Tier:
        assert (
            _PATTERN_STRENGTH[posture.flow_pattern_for(tier)]
            >= _PATTERN_STRENGTH[_FLOOR_PATTERN[tier]]
        )


def test_authored_sub_floor_posture_is_rejected_at_compile() -> None:
    """Non-vacuity: an authored posture that puts restricted below the
    REFERENCE/SEALED floor is refused by compile_posture (via ConfigError)."""
    import pytest

    with pytest.raises(ConfigError, match="floor"):
        compile_posture({"id": "broken", "flow_patterns": {"restricted": "turn_level"}})
