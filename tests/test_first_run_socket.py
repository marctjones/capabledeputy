"""First-run onboarding contract over the real socket.

The Swift onboarding wizard (SetupPlan / SetupPlanStep in Models.swift) decodes
`setup.plan` with permissive defaults, so a renamed key or wrong type would not
crash it — the wizard would silently show an empty or falsely-ready plan. These
tests pin the wire shape and the rule that "ready" is never reported while a
blocking step is unresolved.
"""

from __future__ import annotations

from pathlib import Path

from tests.daemon_integration import running_daemon


async def test_setup_plan_wire_shape_matches_what_the_native_wizard_decodes(
    tmp_path: Path,
) -> None:
    async with running_daemon(tmp_path) as rd:
        plan = await rd.client.call("setup.plan", {})

    assert isinstance(plan["ready"], bool)
    assert isinstance(plan["workflow_ready"], bool)
    workflow = plan["first_workflow"]
    assert isinstance(workflow["id"], str) and workflow["id"]
    assert isinstance(workflow["title"], str) and workflow["title"]
    assert isinstance(workflow["hint"], str) and workflow["hint"]

    steps = plan["steps"]
    assert steps, "a fresh daemon must still describe the setup steps"
    for step in steps:
        assert isinstance(step["id"], str) and step["id"]
        assert isinstance(step["order"], int)
        assert isinstance(step["title"], str) and step["title"]
        assert isinstance(step["status"], str) and step["status"]
        assert isinstance(step["detail"], str) and step["detail"]
        assert isinstance(step["blocking"], bool)


async def test_setup_steps_are_ordered_with_the_daemon_first(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as rd:
        plan = await rd.client.call("setup.plan", {})

    orders = [step["order"] for step in plan["steps"]]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)
    assert plan["steps"][0]["id"] == "daemon"


async def test_fresh_daemon_never_claims_ready_while_a_blocking_step_is_unresolved(
    tmp_path: Path,
) -> None:
    async with running_daemon(tmp_path) as rd:
        plan = await rd.client.call("setup.plan", {})
        check = await rd.client.call("setup.check", {})

    # No planner model is wired in the fresh test daemon, so the model step must block.
    unresolved_blockers = [s for s in plan["steps"] if s["blocking"] and s["status"] != "ok"]
    assert [s["id"] for s in unresolved_blockers] == ["model"], plan["steps"]
    assert plan["ready"] is False
    assert plan["workflow_ready"] is False
    # setup.check and setup.plan must agree, or the wizard and Connections disagree.
    assert check["ready"] == plan["ready"]
    assert check["workflow_ready"] == plan["workflow_ready"]
