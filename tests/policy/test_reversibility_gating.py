"""T090 — Reversibility-weighted gating (FR-019).

Replaces the binary destructive-op gate. Graded thresholds:
  - reversible/system ⇒ AUTO_OK
  - reversible-with-friction OR reversible/non-system ⇒
    REQUIRE_APPROVAL
  - irreversible ⇒ DENY (escalate to override if needed)

Hard rule: `social.*` effect classes are forced irreversible
regardless of the declared reversibility — a sent message is sent.
"""

from __future__ import annotations

from capabledeputy.policy.assurance import (
    EffectGate,
    is_social_commitment,
    reversibility_gate,
)
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)


def _r(degree: ReversibilityDegree, agent: ReversalAgent) -> ReversibilityLabel:
    return ReversibilityLabel(degree=degree, agent=agent)


def test_reversible_system_auto_ok() -> None:
    gate, _, _ = reversibility_gate(
        effect_class="data.write_scratch",
        declared_reversibility=_r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM),
    )
    assert gate == EffectGate.AUTO_OK


def test_reversible_human_requires_approval() -> None:
    gate, _, _ = reversibility_gate(
        effect_class="data.modify_doc",
        declared_reversibility=_r(ReversibilityDegree.REVERSIBLE, ReversalAgent.HUMAN),
    )
    assert gate == EffectGate.REQUIRE_APPROVAL


def test_reversible_external_requires_approval() -> None:
    gate, _, _ = reversibility_gate(
        effect_class="data.api_post",
        declared_reversibility=_r(ReversibilityDegree.REVERSIBLE, ReversalAgent.EXTERNAL),
    )
    assert gate == EffectGate.REQUIRE_APPROVAL


def test_with_friction_requires_approval() -> None:
    gate, _, _ = reversibility_gate(
        effect_class="data.modify_doc",
        declared_reversibility=_r(
            ReversibilityDegree.REVERSIBLE_WITH_FRICTION,
            ReversalAgent.SYSTEM,
        ),
    )
    assert gate == EffectGate.REQUIRE_APPROVAL


def test_irreversible_denies() -> None:
    """DENY here doesn't preclude an override — but ordinary approval
    cannot rescue."""
    gate, _, _ = reversibility_gate(
        effect_class="data.delete_record",
        declared_reversibility=_r(ReversibilityDegree.IRREVERSIBLE, ReversalAgent.SYSTEM),
    )
    assert gate == EffectGate.DENY


def test_social_commitment_recognized() -> None:
    """The hard-coded set covers the canonical social-commitment
    effects — anything that's communicating outward to humans."""
    assert is_social_commitment("social.send_email")
    assert is_social_commitment("social.post_public")
    assert is_social_commitment("social.send_message")
    assert is_social_commitment("social.commit_promise")
    assert not is_social_commitment("data.send_email_internal")


def test_social_commitment_forced_irreversible_even_if_declared_reversible() -> None:
    """FR-019 hard rule: even if the operator (or a tool definition)
    declared `social.send_email` as reversible/system (e.g., because
    the platform technically supports retraction within 30 seconds),
    the gate forces irreversible — reputational fact cannot be
    retracted."""
    declared = _r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM)
    gate, effective, rationale = reversibility_gate(
        effect_class="social.send_email",
        declared_reversibility=declared,
    )
    assert gate == EffectGate.DENY
    assert effective.degree == ReversibilityDegree.IRREVERSIBLE
    assert "social-commitment" in rationale


def test_non_social_irreversible_label_still_returns_label_in_result() -> None:
    """The gate's second tuple element echoes the (possibly
    rewritten) effective reversibility — auditors see what governed
    the gate, not just the input."""
    declared = _r(ReversibilityDegree.IRREVERSIBLE, ReversalAgent.SYSTEM)
    _, effective, rationale = reversibility_gate(
        effect_class="data.delete_record",
        declared_reversibility=declared,
    )
    assert effective == declared
    assert "declared reversibility" in rationale
