"""TUI redesign — the inline console app + armed-decision interaction (§3/§7).

Driven through Textual's Pilot harness. The load-bearing behavioral property:
a decision is resolved ONLY via the app's armed future — keys are inert
otherwise, so a painted fake card cannot approve anything.
"""

from __future__ import annotations

import asyncio

from rich.text import Text

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.rules import Decision
from capabledeputy.tui.inline.app import InlineConsole
from capabledeputy.tui.inline.model import ApprovalPrompt
from capabledeputy.tui.inline.status import TrustState


class _NullDriver:
    async def run_turn(self, text: str, console: InlineConsole) -> None:
        return None


def _app() -> InlineConsole:
    return InlineConsole(_NullDriver(), trust=TrustState(session_name="triage"))


def _prompt(decision: Decision) -> ApprovalPrompt:
    return ApprovalPrompt(
        PolicyDecision(decision=decision, rule="health-meets-egress", reason="recap"),
        action_kind=CapabilityKind.SEND_EMAIL,
        target="dr@x.example",
    )


async def test_app_mounts_and_shows_session() -> None:
    app = _app()
    async with app.run_test():
        status = app._last_status
        assert isinstance(status, Text)
        assert "triage" in status.plain
        assert app._marker.glyph in status.plain  # anti-spoof marker on chrome


async def test_keys_are_inert_without_an_armed_decision() -> None:
    app = _app()
    async with app.run_test() as pilot:
        # No armed decision → pressing approve must do nothing (and not crash).
        await pilot.press("a")
        assert app._armed_future is None


async def test_armed_approve_resolves_only_via_the_future() -> None:
    app = _app()
    async with app.run_test() as pilot:
        task = asyncio.create_task(app.request_decision(_prompt(Decision.REQUIRE_APPROVAL)))
        await pilot.pause()
        assert app._armed is not None  # armed; input disabled
        await pilot.press("a")
        assert await asyncio.wait_for(task, timeout=2) == "approve"
        assert app._armed is None  # disarmed after resolution


async def test_armed_deny_resolves() -> None:
    app = _app()
    async with app.run_test() as pilot:
        task = asyncio.create_task(app.request_decision(_prompt(Decision.REQUIRE_APPROVAL)))
        await pilot.pause()
        await pilot.press("d")
        assert await asyncio.wait_for(task, timeout=2) == "deny"


async def test_flow_lineage_and_slash_commands() -> None:
    from capabledeputy.tui.inline.app import FlowScreen
    from capabledeputy.tui.inline.model import ToolDecision

    app = _app()
    async with app.run_test() as pilot:
        # a tool call feeds the lineage:
        app.append(
            ToolDecision(
                PolicyDecision(decision=Decision.ALLOW),
                action_kind=CapabilityKind.READ_FS,
                target="labs.pdf",
            ),
        )
        assert app._flow and "labs.pdf" in app._flow[0][0]
        # /flow opens the lineage screen; esc closes it:
        app._handle_command("/flow")
        await pilot.pause()
        assert isinstance(app.screen, FlowScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, FlowScreen)
        # /help and an unknown command don't crash:
        app._handle_command("/help")
        app._handle_command("/bogus")


async def test_halt_kill_switch_denies_an_armed_decision() -> None:
    app = _app()
    async with app.run_test() as pilot:
        task = asyncio.create_task(app.request_decision(_prompt(Decision.REQUIRE_APPROVAL)))
        await pilot.pause()
        await pilot.press("ctrl+k")
        # halt resolves the pending decision toward deny — never toward allow.
        assert await asyncio.wait_for(task, timeout=2) == "deny"
