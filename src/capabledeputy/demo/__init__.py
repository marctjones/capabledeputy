"""Interactive REPL demo scenarios.

A scenario bundles the seed data (inbox messages, calendar events,
memory entries), the initial session shape (intent + capabilities),
and a short intro string so a user can `capdep demo start <name>`
and immediately have a session worth talking to.

The scenarios are intentionally small and hand-curated — they're the
guided tour for someone who has just cloned the repo.
"""

from capabledeputy.demo.scenarios import (
    SCENARIOS,
    CalendarSeed,
    InboxSeed,
    MemorySeed,
    Scenario,
    ScenarioCapability,
    ScenarioNotFoundError,
    get_scenario,
)

__all__ = [
    "SCENARIOS",
    "CalendarSeed",
    "InboxSeed",
    "MemorySeed",
    "Scenario",
    "ScenarioCapability",
    "ScenarioNotFoundError",
    "get_scenario",
]
