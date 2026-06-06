"""#15 — structured four-axis label wire format on approval bundles.

The legacy bundle wire fields (`inherent_labels`/`arg_labels`) are flat
category/level strings that LOSE tier + risk_ids and merge categories
with provenance into one set. v2 adds structured `inherent_tags`/`arg_tags`
(`LabelState`) alongside them. These tests prove:
  - a v2 bundle round-trips the structured tags losslessly (tier +
    risk_ids preserved);
  - the flat fields are still emitted (back-compat for v1 readers);
  - a v1 bundle dict (no structured fields) still deserializes, with the
    structured tags defaulting to empty.
"""

from __future__ import annotations

from capabledeputy.approval.bundle import (
    BUNDLE_FORMAT_VERSION,
    BundledApproval,
    GateState,
    WorkflowImpact,
    WorkflowStep,
)
from capabledeputy.daemon.bundle_handlers import _impact_from_dict
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier

_FINANCIAL = LabelState(
    a=frozenset(
        {
            CategoryTag(
                category="financial",
                tier=Tier.REGULATED,
                risk_ids=("RISK-EXFIL",),
                assignment_provenance="source-declared",
            ),
        },
    ),
)
_UNTRUSTED = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))


def _bundle() -> WorkflowImpact:
    return WorkflowImpact(
        program_hash="abc",
        steps=[
            WorkflowStep(
                step_index=0,
                tool_name="email.send",
                args={"to": "a@b.com"},
                arg_labels=frozenset({"financial"}),
                decision="require_approval",
                inherent_labels=frozenset({"financial", "external-untrusted"}),
                rule="financial-meets-email",
                reason="r",
                line=1,
                inherent_tags=_FINANCIAL,
                arg_tags=_UNTRUSTED,
            ),
        ],
        gates=[
            BundledApproval(
                step_index=0,
                tool_name="email.send",
                args={"to": "a@b.com"},
                arg_labels=frozenset({"financial"}),
                rule="financial-meets-email",
                reason="r",
                arg_tags=_UNTRUSTED,
                state=GateState.PENDING,
            ),
        ],
    )


def test_to_dict_carries_format_version_and_structured_tags() -> None:
    d = _bundle().to_dict()
    assert d["format_version"] == BUNDLE_FORMAT_VERSION == 2
    step = d["steps"][0]
    # Flat fields still present (v1 back-compat for old readers).
    assert sorted(step["inherent_labels"]) == ["external-untrusted", "financial"]
    # Structured fields carry the full four-axis shape.
    assert step["inherent_tags"]["a"][0]["category"] == "financial"
    assert step["inherent_tags"]["a"][0]["tier"] == "regulated"
    assert step["inherent_tags"]["a"][0]["risk_ids"] == ["RISK-EXFIL"]
    assert d["gates"][0]["arg_tags"]["b"][0]["level"] == "external-untrusted"


def test_round_trip_preserves_tier_and_risk_ids() -> None:
    """The lossy flat strings drop tier+risk_ids; the structured field
    round-trips them through to_dict -> _impact_from_dict."""
    back = _impact_from_dict(_bundle().to_dict())
    step = back.steps[0]
    (cat,) = step.inherent_tags.a
    assert cat.category == "financial"
    assert cat.tier is Tier.REGULATED
    assert cat.risk_ids == ("RISK-EXFIL",)
    (lvl,) = back.gates[0].arg_tags.b
    assert lvl.level is ProvenanceLevel.EXTERNAL_UNTRUSTED


def test_v1_bundle_without_structured_tags_still_deserializes() -> None:
    """A pre-v2 bundle dict (no inherent_tags/arg_tags) deserializes;
    the structured tags default to empty LabelState."""
    d = _bundle().to_dict()
    for step in d["steps"]:
        step.pop("inherent_tags")
        step.pop("arg_tags")
    for gate in d["gates"]:
        gate.pop("arg_tags")
    d.pop("format_version")
    back = _impact_from_dict(d)
    assert back.steps[0].inherent_tags == LabelState()
    assert back.steps[0].arg_tags == LabelState()
    assert back.gates[0].arg_tags == LabelState()
    # Flat fields still came through.
    assert "financial" in back.steps[0].inherent_labels
