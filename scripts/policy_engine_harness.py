#!/usr/bin/env python
"""Smoke suite — a small cross-section of the policy chokepoint.

Kept as the quick "is enforcement alive" check. The exhaustive
coverage lives in the themed scripts/policy_*.py suites; run them all
via scripts/run_all_policy.py.

Run:  uv run python scripts/policy_engine_harness.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _policy_harness import (
    Expect,
    Scenario,
    final,
    run_suite,
    tc,
    tool_turn,
)

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier

TITLE = "smoke"

SCENARIOS: list[Scenario] = [
    Scenario(
        name="allow-within-capability",
        why="Held capability + no tainting labels -> ALLOW.",
        caps=frozenset(
            {
                Capability(kind=CapabilityKind.WRITE_FS, pattern="*"),
                Capability(kind=CapabilityKind.READ_FS, pattern="*"),
            },
        ),
        responses=[
            tool_turn(
                "save then read",
                tc("w1", "memory.write", key="note", value="hello"),
                tc("r1", "memory.read", key="note"),
            ),
            final(),
        ],
        expect=[
            Expect("memory.write", "allow"),
            Expect("memory.read", "allow"),
        ],
    ),
    Scenario(
        name="deny-health-meets-egress",
        why="Read health-labeled data, then purchase egress -> DENY.",
        caps=frozenset(
            {
                Capability(kind=CapabilityKind.READ_FS, pattern="*"),
                Capability(
                    kind=CapabilityKind.QUEUE_PURCHASE,
                    pattern="*",
                    max_amount=1000,
                ),
            },
        ),
        pre=lambda app: app.memory.write(
            "rx",
            "lisinopril 10mg",
            LabelState(a=frozenset({CategoryTag("health", Tier.REGULATED)})),
        ),
        responses=[
            tool_turn("read rx", tc("h1", "memory.read", key="rx")),
            tool_turn(
                "order refill",
                tc("p1", "purchase.queue", vendor="pharmacy", item="refill", amount=20),
            ),
            final("blocked"),
        ],
        expect=[
            Expect("memory.read", "allow"),
            Expect("purchase.queue", "deny", "health-meets-egress"),
        ],
    ),
    Scenario(
        name="require-approval-financial-purchase",
        why="Financial-labeled session queuing a purchase -> REQUIRE_APPROVAL.",
        caps=frozenset(
            {
                Capability(
                    kind=CapabilityKind.QUEUE_PURCHASE,
                    pattern="*",
                    max_amount=10_000,
                ),
            },
        ),
        session_labels=frozenset({"confidential.financial"}),
        responses=[
            tool_turn(
                "queue purchase",
                tc("p1", "purchase.queue", vendor="amazon", item="desk", amount=420),
            ),
            final("awaiting approval"),
        ],
        expect=[
            Expect("purchase.queue", "require_approval", "financial-meets-purchase"),
        ],
    ),
]


async def main() -> int:
    return await run_suite(TITLE, SCENARIOS)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
