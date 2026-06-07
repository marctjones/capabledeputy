#!/usr/bin/env python
"""REQUIRE_APPROVAL coverage — the human-in-the-loop gates.

  - financial-meets-purchase (CONFIDENTIAL_FINANCIAL x EGRESS_PURCHASE)
    is the one conflict rule whose decision is REQUIRE_APPROVAL.
  - destructive-op-needs-approval: MODIFY/DELETE tools (memory.update,
    memory.delete, calendar.update_event, calendar.delete_event) require
    approval unless the capability sets allows_destructive=True. Here the
    caps deliberately do NOT, so every one gates.

The allows_destructive=True bypass (-> ALLOW) is covered in policy_allow.

Run:  uv run python scripts/policy_require_approval.py
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
from capabledeputy.policy.labels import LabelState

TITLE = "REQUIRE_APPROVAL paths"

K = CapabilityKind


def _seed_mem(app: object) -> None:
    app.memory.write("stale", "v", LabelState())  # type: ignore[attr-defined]
    app.memory.write("doomed", "v", LabelState())  # type: ignore[attr-defined]


SCENARIOS: list[Scenario] = [
    Scenario(
        name="financial-meets-purchase",
        why="CONFIDENTIAL_FINANCIAL session + purchase egress -> REQUIRE_APPROVAL.",
        caps=frozenset(
            {Capability(kind=K.QUEUE_PURCHASE, pattern="*", max_amount=10_000)},
        ),
        session_labels=frozenset({"confidential.financial"}),
        responses=[
            tool_turn(
                "queue purchase",
                tc("p", "purchase.queue", vendor="amazon", item="desk", amount=420),
            ),
            final("awaiting approval"),
        ],
        expect=[
            Expect("purchase.queue", "require_approval", "financial-meets-purchase"),
        ],
    ),
    Scenario(
        name="destructive-fs-gate",
        why="MODIFY_FS / DELETE_FS without allows_destructive -> REQUIRE_APPROVAL.",
        caps=frozenset(
            {
                Capability(kind=K.MODIFY_FS, pattern="*"),
                Capability(kind=K.DELETE_FS, pattern="*"),
            },
        ),
        pre=_seed_mem,
        responses=[
            tool_turn(
                "modify then delete",
                tc("mu", "memory.update", key="stale", value="fresh"),
            ),
            tool_turn("delete", tc("md", "memory.delete", key="doomed")),
            final("awaiting approval"),
        ],
        expect=[
            Expect("memory.update", "require_approval", "destructive-op-needs-approval"),
            Expect("memory.delete", "require_approval", "destructive-op-needs-approval"),
        ],
    ),
    Scenario(
        name="destructive-calendar-gate",
        why="MODIFY_CAL / DELETE_CAL without allows_destructive -> REQUIRE_APPROVAL.",
        caps=frozenset(
            {
                Capability(kind=K.MODIFY_CAL, pattern="*"),
                Capability(kind=K.DELETE_CAL, pattern="*"),
            },
        ),
        responses=[
            tool_turn(
                "update event",
                tc(
                    "cu",
                    "calendar.update_event",
                    id="00000000-0000-0000-0000-000000000001",
                    title="moved",
                ),
            ),
            tool_turn(
                "delete event",
                tc(
                    "cd",
                    "calendar.delete_event",
                    id="00000000-0000-0000-0000-000000000002",
                ),
            ),
            final("awaiting approval"),
        ],
        expect=[
            Expect(
                "calendar.update_event",
                "require_approval",
                "destructive-op-needs-approval",
            ),
            Expect(
                "calendar.delete_event",
                "require_approval",
                "destructive-op-needs-approval",
            ),
        ],
    ),
]


async def main() -> int:
    return await run_suite(TITLE, SCENARIOS)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
