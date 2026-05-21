"""Named hook registry (spec 004 P0 T020).

PolicyContext today carries primitive tuples directly (inspectors,
decision_inspectors, declassifiers). That works but doesn't surface
the lifecycle points clearly — operators see "register an inspector"
rather than "register at the at_ingest.value_in hook".

HookRegistry gives a named-hooks vocabulary so operators can
configure policy primitives via YAML referencing the hook name,
the chokepoint dispatches each registered primitive at the right
lifecycle moment, and audit trails carry the hook name for clarity.

Named hooks:
  at_ingest.value_in              — RaiseOnlyInspectors fire on tool result
  at_ingest.declassifier_chain    — DeclassifyingTransformers
  at_chokepoint.pre_decide        — pre-decision processing (rare)
  at_chokepoint.decision          — DecisionInspectors run here
  at_chokepoint.post_decide       — post-decision audit hooks
  at_dispatch.pre_dispatch        — about to invoke a tool handler
  at_dispatch.post_dispatch       — handler returned
  at_session.spawn                — new session created
  at_session.terminate            — session ending

Operators register via:
  registry.register("at_chokepoint.decision", inspector)

The chokepoint queries:
  for inspector in registry.get("at_chokepoint.decision"):
      ...
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Standard hook names. Operators can register at any of these.
# Adding more is an additive change; removing one is a breaking change.
STANDARD_HOOKS = frozenset(
    {
        "at_ingest.value_in",
        "at_ingest.declassifier_chain",
        "at_chokepoint.pre_decide",
        "at_chokepoint.decision",
        "at_chokepoint.post_decide",
        "at_dispatch.pre_dispatch",
        "at_dispatch.post_dispatch",
        "at_session.spawn",
        "at_session.terminate",
    },
)


class HookError(RuntimeError):
    """Operator tried to register at an unknown hook name, or other
    structural mistake."""


@dataclass
class HookRegistry:
    """Operator-curated registry of primitives by lifecycle hook name.

    Registration is order-preserving — if an operator registers
    inspector A before inspector B, they run in that order at the
    same hook. Across hooks, the runtime decides the firing order
    (each primitive runs at its hook's lifecycle moment).
    """

    _hooks: dict[str, list[Any]] = field(default_factory=lambda: defaultdict(list))

    def register(self, hook_name: str, primitive: Any) -> None:
        """Register `primitive` at the named hook.

        Raises HookError if hook_name is not a STANDARD_HOOKS member —
        prevents typos from silently dropping primitives.
        """
        if hook_name not in STANDARD_HOOKS:
            raise HookError(
                f"unknown hook {hook_name!r}; valid: {sorted(STANDARD_HOOKS)}",
            )
        self._hooks[hook_name].append(primitive)

    def get(self, hook_name: str) -> tuple[Any, ...]:
        """Return the ordered tuple of primitives at `hook_name`.

        Unknown hook name is permissive on read (returns empty) so
        chokepoint code can query freely without knowing which hooks
        the operator populated.
        """
        return tuple(self._hooks.get(hook_name, ()))

    def all_registered_hooks(self) -> tuple[str, ...]:
        """Sorted names of every hook with at least one registration."""
        return tuple(sorted(name for name, lst in self._hooks.items() if lst))

    def is_empty(self) -> bool:
        """True if no primitive has been registered anywhere."""
        return not any(self._hooks.values())

    def total_primitives(self) -> int:
        """Sum of registrations across all hooks."""
        return sum(len(lst) for lst in self._hooks.values())


def build_registry_from_policy_context(
    policy_context: Any,
) -> HookRegistry:
    """Bridge: convert the existing PolicyContext tuples into a
    HookRegistry. Lets the chokepoint and the named-hook surface
    coexist during the transition — eventually PolicyContext's
    tuple fields will defer to the registry.

    Tuple → hook mapping:
      inspectors           → at_ingest.value_in
      declassifiers        → at_ingest.declassifier_chain
      decision_inspectors  → at_chokepoint.decision
    """
    registry = HookRegistry()
    for inspector in getattr(policy_context, "inspectors", ()) or ():
        registry.register("at_ingest.value_in", inspector)
    for declassifier in getattr(policy_context, "declassifiers", ()) or ():
        registry.register("at_ingest.declassifier_chain", declassifier)
    for decision_inspector in getattr(policy_context, "decision_inspectors", ()) or ():
        registry.register("at_chokepoint.decision", decision_inspector)
    return registry
