"""Tests for the interactive demo scaffolding.

Covers: every built-in scenario is well-formed, apply_scenario
populates the right stores with the right counts, the demo.start
RPC handler returns the expected payload, and an unknown scenario
returns a clear error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.demo_handlers import make_demo_handlers
from capabledeputy.demo import SCENARIOS, get_scenario
from capabledeputy.demo.scenarios import ScenarioNotFoundError
from capabledeputy.demo.seed import apply_scenario


@pytest.fixture
async def app(tmp_path: Path) -> App:
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await a.startup()
    return a


def test_every_scenario_has_one_line_intro_and_intent() -> None:
    assert len(SCENARIOS) >= 3
    for s in SCENARIOS.values():
        assert s.one_line, f"{s.name} missing one_line"
        assert s.intro, f"{s.name} missing intro"
        assert s.intent, f"{s.name} missing intent"
        assert s.capabilities, f"{s.name} has no capabilities"


def test_get_scenario_unknown_raises() -> None:
    with pytest.raises(ScenarioNotFoundError):
        get_scenario("does-not-exist")


async def test_apply_daily_briefing_seeds_inbox_and_calendar(app: App) -> None:
    scenario = get_scenario("daily-briefing")
    result = await apply_scenario(app, scenario)
    assert result.inbox_count == len(scenario.inbox)
    assert result.calendar_count == len(scenario.calendar)
    assert result.capabilities_granted == len(scenario.capabilities)
    # stores are populated
    assert len(app.inbox.all()) == len(scenario.inbox)
    assert len(app.calendar.all()) == len(scenario.calendar)
    # session was created with caps granted
    session = app.graph.get(result.session_id)
    assert len(session.capability_set) == len(scenario.capabilities)
    assert session.intent == scenario.intent


async def test_apply_accountant_seeds_memory(app: App) -> None:
    scenario = get_scenario("accountant")
    result = await apply_scenario(app, scenario)
    assert result.memory_count == len(scenario.memory)
    for entry in scenario.memory:
        stored = app.memory.read(entry.key)
        assert stored is not None
        assert stored.label_state == entry.label_state


async def test_apply_untrusted_research_grants_web_fetch_cap(app: App) -> None:
    from capabledeputy.policy.capabilities import CapabilityKind

    scenario = get_scenario("untrusted-research")
    result = await apply_scenario(app, scenario)
    session = app.graph.get(result.session_id)
    kinds = {c.kind for c in session.capability_set}
    assert CapabilityKind.WEB_FETCH in kinds


async def test_demo_list_scenarios_handler_returns_all(app: App) -> None:
    handlers = make_demo_handlers(app)
    result = await handlers["demo.list_scenarios"]({})
    names = {s["name"] for s in result["scenarios"]}
    assert names == set(SCENARIOS.keys())


async def test_demo_start_handler_returns_session_id_and_intro(app: App) -> None:
    handlers = make_demo_handlers(app)
    result = await handlers["demo.start"]({"name": "daily-briefing"})
    assert "session_id" in result
    assert result["scenario"]["name"] == "daily-briefing"
    assert result["scenario"]["intro"]
    assert result["seed_counts"]["inbox"] >= 1
    # The returned session id is real and present in the graph.
    from uuid import UUID

    assert UUID(result["session_id"]) in app.graph._sessions


async def test_demo_start_handler_unknown_scenario_returns_error(app: App) -> None:
    handlers = make_demo_handlers(app)
    result = await handlers["demo.start"]({"name": "nope"})
    assert "error" in result
    assert "unknown scenario" in result["error"]
