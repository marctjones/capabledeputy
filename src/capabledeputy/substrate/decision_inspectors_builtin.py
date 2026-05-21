"""Builtin DecisionInspector implementations.

These are reference inspectors operators can register out of the box.
Each is a pure function of (action, session, proposed_outcome) →
relax/tighten/None. Together with operator-authored inspectors they
form the policy refinement layer above the standard chokepoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from capabledeputy.policy.rules import Decision
from capabledeputy.substrate.decision_inspector_port import (
    DecisionRelax,
    DecisionTighten,
)


@dataclass(frozen=True)
class SelfEgressRelaxer:
    """Auto-allow email/calendar/communication actions targeting the
    operator's own addresses.

    Rationale: when the operator IS the recipient, the standard
    REQUIRE_APPROVAL prompt for SEND_EMAIL is friction without benefit
    — the "social commitment" check exists to catch unintended external
    communication, not self-communication.

    Configuration:
        self_addresses: emails / handles that count as "self"
        action_kinds: which actions are eligible (default: SEND_EMAIL)
    """

    name: str = "SelfEgressRelaxer"
    self_addresses: frozenset[str] = field(default_factory=frozenset)
    action_kinds: frozenset[str] = field(default_factory=lambda: frozenset({"SEND_EMAIL"}))

    def inspect(
        self,
        *,
        action: Any,
        session: Any,
        proposed_outcome: Any,
    ) -> DecisionRelax | DecisionTighten | None:
        action_kind = getattr(action.kind, "value", str(action.kind))
        if action_kind not in self.action_kinds:
            return None
        target = (getattr(action, "target", "") or "").lower().strip()
        if not target:
            return None
        if target not in {a.lower() for a in self.self_addresses}:
            return None
        # Only relax if the proposed decision is REQUIRE_APPROVAL —
        # don't override OVERRIDE_REQUIRED (the chokepoint set that
        # because something more serious blocked the action).
        if proposed_outcome.decision != Decision.REQUIRE_APPROVAL:
            return None
        return DecisionRelax(
            to=Decision.ALLOW,
            rule="self-egress-auto-approved",
            rationale=f"Recipient {target} is operator's own address.",
        )


@dataclass(frozen=True)
class AfterHoursPurchaseTightener:
    """Require approval for late-night purchases that would otherwise
    auto-allow under the operator's risk-preference dial.

    Rationale: tired-operator or compromised-session risk is higher
    overnight; the extra prompt is cheap insurance.

    Configuration:
        start_hour_utc / end_hour_utc: the "after-hours" window
        action_kinds: which actions trigger (default: QUEUE_PURCHASE)
    """

    name: str = "AfterHoursPurchaseTightener"
    start_hour_utc: int = 22  # 10pm
    end_hour_utc: int = 6  # 6am
    action_kinds: frozenset[str] = field(default_factory=lambda: frozenset({"QUEUE_PURCHASE"}))

    def inspect(
        self,
        *,
        action: Any,
        session: Any,
        proposed_outcome: Any,
    ) -> DecisionRelax | DecisionTighten | None:
        action_kind = getattr(action.kind, "value", str(action.kind))
        if action_kind not in self.action_kinds:
            return None
        if proposed_outcome.decision != Decision.ALLOW:
            return None
        hour = datetime.now(UTC).hour
        # The window wraps midnight when start > end (e.g., 22 → 6).
        if self.start_hour_utc > self.end_hour_utc:
            in_window = hour >= self.start_hour_utc or hour < self.end_hour_utc
        else:
            in_window = self.start_hour_utc <= hour < self.end_hour_utc
        if not in_window:
            return None
        return DecisionTighten(
            to=Decision.REQUIRE_APPROVAL,
            rule="after-hours-purchase-scrutiny",
            rationale=(
                f"Purchase at hour={hour} UTC falls in the "
                f"after-hours window [{self.start_hour_utc}, {self.end_hour_utc})."
            ),
        )
