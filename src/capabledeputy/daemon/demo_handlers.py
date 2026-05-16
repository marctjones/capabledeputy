"""RPC handlers for the interactive demo scaffolding.

Two endpoints:

  - `demo.list_scenarios`: returns the names + one-line summaries of
    every built-in scenario.
  - `demo.start`: given a scenario name, seeds the in-memory stores
    and returns the new session id plus the scenario's intro, suggested
    prompts, and security note so the REPL can render the briefing.
"""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler
from capabledeputy.demo.scenarios import SCENARIOS, ScenarioNotFoundError, get_scenario
from capabledeputy.demo.seed import apply_scenario


def make_demo_handlers(app: App) -> dict[str, Handler]:
    async def list_scenarios(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "scenarios": [{"name": s.name, "one_line": s.one_line} for s in SCENARIOS.values()],
        }

    async def start(params: dict[str, Any]) -> dict[str, Any]:
        try:
            scenario = get_scenario(str(params["name"]))
        except ScenarioNotFoundError as e:
            return {"error": f"unknown scenario: {e.name}"}
        result = await apply_scenario(app, scenario)
        return {
            "session_id": str(result.session_id),
            "scenario": {
                "name": scenario.name,
                "intro": scenario.intro,
                "intent": scenario.intent,
                "suggested_prompts": list(scenario.suggested_prompts),
                "security_note": scenario.security_note,
            },
            "seed_counts": {
                "inbox": result.inbox_count,
                "calendar": result.calendar_count,
                "memory": result.memory_count,
                "capabilities": result.capabilities_granted,
            },
        }

    return {
        "demo.list_scenarios": list_scenarios,
        "demo.start": start,
    }
