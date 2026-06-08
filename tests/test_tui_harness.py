"""TUI redesign — the automation harness: scripted scenario tests with no
terminal.

Demonstrates the intended pattern for automated UI + server scripts: run a
driver against a headless console, auto-answer decisions with a pluggable
policy, and assert on a structured transcript. Swap DemoDriver for a real
daemon-backed driver and the same script tests the full stack.
"""

from __future__ import annotations

import asyncio

from capabledeputy.tui.inline.demo import DemoDriver
from capabledeputy.tui.inline.harness import (
    ConsoleHarness,
    approve_all,
    by_rule,
    deny_all,
)


async def test_scripted_approve_scenario() -> None:
    h = ConsoleHarness(DemoDriver(delay=0), decide=approve_all)
    await h.send("recap my labs and email Dr Lee")

    # exactly one gated decision, on the real engine floor:
    assert [e.rule for e in h.decisions()] == ["health-meets-egress"]
    # the untrusted block was quarantined (escape-free) — the safety property,
    # asserted from an automated script:
    untrusted = h.events("untrusted")
    assert untrusted and all("\x1b" not in e.text for e in untrusted)
    # approved → sent:
    assert any("sent" in e.text for e in h.events("outcome"))


async def test_scripted_deny_scenario() -> None:
    h = ConsoleHarness(DemoDriver(delay=0), decide=deny_all)
    await h.send("x")
    assert h.events("resolved")[0].choice == "deny"
    assert any("not sent" in e.text for e in h.events("outcome"))


async def test_rule_based_decider() -> None:
    h = ConsoleHarness(
        DemoDriver(delay=0),
        decide=by_rule({"health-meets-egress": "approve"}, default="deny"),
    )
    await h.send("x")
    assert h.events("resolved")[0].choice == "approve"


def test_transcript_is_structured_and_ordered() -> None:
    h = ConsoleHarness(DemoDriver(delay=0))
    asyncio.run(h.send("x"))
    kinds = [e.kind for e in h.transcript]
    # the full shape of a turn is observable + assertable:
    for expected in ("tool", "untrusted", "advisory", "decision", "resolved", "outcome"):
        assert expected in kinds
    # a tool event carries the engine decision + target:
    tool = next(e for e in h.transcript if e.kind == "tool")
    assert tool.target and tool.decision is not None
