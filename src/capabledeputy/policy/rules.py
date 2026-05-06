"""Brewer-Nash conflict rules for session label sets (DESIGN.md §7.2).

A rule fires when the session's effective label set contains at least
one trigger label AND at least one conflict label. Firing produces
either DENY (deny without declassifier) or REQUIRE_APPROVAL (the
Clark-Wilson gate). The set defined here is the v0.1 MVP scope.

Note: rule 5 from DESIGN.md §7.2 ("untrusted.external content used as
tool argument → wrap in declassifier check") is per-argument, not
per-session, and is enforced at the tool-dispatch layer in Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from capabledeputy.policy.labels import Label


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class ConflictRule:
    name: str
    triggers: frozenset[Label]
    conflicts: frozenset[Label]
    decision: Decision

    def fires(self, label_set: frozenset[Label]) -> bool:
        return bool(self.triggers & label_set) and bool(self.conflicts & label_set)


CONFLICT_RULES: tuple[ConflictRule, ...] = (
    ConflictRule(
        name="untrusted-meets-egress",
        triggers=frozenset({Label.UNTRUSTED_EXTERNAL, Label.UNTRUSTED_USER_INPUT}),
        conflicts=frozenset({Label.EGRESS_EMAIL, Label.EGRESS_PURCHASE}),
        decision=Decision.DENY,
    ),
    ConflictRule(
        name="health-meets-egress",
        triggers=frozenset({Label.CONFIDENTIAL_HEALTH}),
        conflicts=frozenset({Label.EGRESS_EMAIL, Label.EGRESS_PURCHASE}),
        decision=Decision.DENY,
    ),
    ConflictRule(
        name="financial-meets-email",
        triggers=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        conflicts=frozenset({Label.EGRESS_EMAIL}),
        decision=Decision.DENY,
    ),
    ConflictRule(
        name="financial-meets-purchase",
        triggers=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        conflicts=frozenset({Label.EGRESS_PURCHASE}),
        decision=Decision.REQUIRE_APPROVAL,
    ),
)
