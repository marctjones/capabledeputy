"""Actions: concrete attempts to use a capability.

An Action describes what the runtime is being asked to do on the LLM's
behalf. The policy engine checks whether the session holds a matching
capability and whether granting it would violate any conflict rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from capabledeputy.policy.capabilities import CapabilityKind


@dataclass(frozen=True)
class Action:
    kind: CapabilityKind
    target: str
    amount: int | None = None
