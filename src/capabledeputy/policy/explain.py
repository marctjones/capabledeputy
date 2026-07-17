"""#386 — explain a decision: the payoff of declarative-first policy.

`docs/policy-authoring-design.md` §8: because policy is data, the engine can tell
a human WHY a hypothetical decision goes the way it does — impossible if policy
were opaque code. `explain_decision` runs the real `decide()` for a scenario and
returns the outcome, the rule/floor that decided it, and which precedence level
(design §5 lattice) that rule sits at.

This is the offline "why would this be denied?" core; `cli/policy.py` renders it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.engine import decide
from capabledeputy.policy.envelope import EnvelopeSet, RiskPreference
from capabledeputy.policy.labels import LabelState
from capabledeputy.policy.precedence import PrecedenceLevel
from capabledeputy.policy.reversibility import ReversibilityLabel
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)

# Rule ids that come from a structural FLOOR (always-on, non-negotiable). Kept in
# sync with engine.py's floor rule constants.
_FLOOR_RULES: frozenset[str] = frozenset(
    {
        "untrusted-meets-egress",
        "health-meets-egress",
        "financial-meets-email",
        "financial-meets-purchase",
        "confidential-meets-web-fetch",
        "regulated-data-meets-web-fetch",
        "control-plane-tainted-session",
        "clearance-refused",
        "integrity-floor-refused",
        "reversibility-irreversible",
        "sandbox-no-actuator",
        "devbox-no-manager",
        "binding-unbound",
        "orphan-risk-citation",
    },
)


def _classify(rule: str | None) -> PrecedenceLevel:
    """Map a decision rule id to the precedence level it came from (design §5)."""
    if rule is None:
        return PrecedenceLevel.RULE  # legacy capability/destructive gate
    if rule in _FLOOR_RULES:
        return PrecedenceLevel.FLOOR
    if rule.startswith("v2:"):
        return PrecedenceLevel.RULE
    if rule.startswith("envelope-dial"):
        return PrecedenceLevel.POSTURE
    if "requirement" in rule or rule.startswith("builtin."):
        return PrecedenceLevel.REQUIREMENT
    # first-use / rate-limit / expiry etc. are engine gates, not authored rules.
    return PrecedenceLevel.RULE


@dataclass(frozen=True)
class Explanation:
    decision: Decision
    rule: str | None
    reason: str | None
    level: PrecedenceLevel

    def summary(self) -> str:
        verb = {
            Decision.ALLOW: "would be ALLOWED",
            Decision.WARN: "would WARN",
            Decision.REQUIRE_APPROVAL: "needs human APPROVAL",
            Decision.OVERRIDE_REQUIRED: "needs an operator OVERRIDE",
            Decision.DENY: "would be DENIED",
        }[self.decision]
        origin = f" by the {self.level.name.lower()} `{self.rule}`" if self.rule else ""
        because = f" — {self.reason}" if self.reason else ""
        return f"This action {verb}{origin}{because}"


def _broad_cap(kind: CapabilityKind) -> frozenset[Capability]:
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


def explain_decision(
    *,
    labels: LabelState,
    kind: CapabilityKind,
    target: str,
    effect_class: str | None = None,
    reversibility: ReversibilityLabel | None = None,
    risk_preference: RiskPreference | None = None,
    envelope_set: EnvelopeSet | None = None,
    clearance_max_tier: Tier | None = None,
) -> Explanation:
    """Run `decide()` for a hypothetical scenario and explain the outcome.

    Uses a wildcard capability so the explanation reflects the POLICY (labels x
    action x floors x dial), not an incidental missing-capability DENY. Excludes
    override grants (a static what-if)."""
    result = decide(
        _broad_cap(kind),
        Action(kind=kind, target=target),
        now=_NOW,
        labels=labels,
        effect_class=effect_class,
        effective_reversibility=reversibility,
        risk_preference=risk_preference,
        envelope_set=envelope_set,
        clearance_max_tier=clearance_max_tier,
        override_grants=None,
    )
    return Explanation(
        decision=result.decision,
        rule=result.rule,
        reason=result.reason,
        level=_classify(result.rule),
    )
