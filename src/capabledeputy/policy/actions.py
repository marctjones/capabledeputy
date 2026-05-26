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
    # Issue #35 / #37 — Action.kind accepts both built-in CapabilityKind
    # enum members AND custom-kind strings registered via servers.d/*.yaml.
    # Both compare correctly to str at the engine; downstream
    # serialization uses `kind_name()` for the bare name.
    kind: CapabilityKind | str
    target: str
    amount: int | None = None
