#!/usr/bin/env python
"""v0.7 capability-constraint coverage — the per-capability gates that
deny independently of information-flow labels.

  - capability-expired               (Capability.expires_at in the past)
  - rate-limit-exceeded              (Capability.rate_limit window cap)
  - capability-revoked-by-prior-use  (Capability.revoked_by tool-identity)

Each is evaluated deterministically at the chokepoint against the real
clock / the session's recorded use log — never by the LLM.

Run:  uv run python scripts/policy_constraints.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
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

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    RateLimit,
)

TITLE = "capability constraints (v0.7)"

K = CapabilityKind
_PAST = datetime.now(UTC) - timedelta(hours=1)


def _seed(app: object) -> None:
    app.memory.write("k", "v", frozenset())  # type: ignore[attr-defined]


SCENARIOS: list[Scenario] = [
    Scenario(
        name="capability-expired",
        why="READ_FS capability whose expires_at is in the past -> DENY.",
        caps=frozenset({Capability(kind=K.READ_FS, pattern="*", expires_at=_PAST)}),
        pre=_seed,
        responses=[
            tool_turn("read", tc("r", "memory.read", key="k")),
            final("blocked"),
        ],
        expect=[Expect("memory.read", "deny", "capability-expired")],
    ),
    Scenario(
        name="rate-limit-exceeded",
        why=(
            "READ_FS capped at 1 use / hour: first read ALLOWED, the "
            "second within the window -> DENY (rate-limit-exceeded)."
        ),
        caps=frozenset(
            {
                Capability(
                    kind=K.READ_FS,
                    pattern="*",
                    rate_limit=RateLimit(max_uses=1, window_seconds=3600),
                ),
            },
        ),
        pre=_seed,
        responses=[
            tool_turn(
                "read twice",
                tc("r1", "memory.read", key="k"),
                tc("r2", "memory.read", key="k"),
            ),
            final("second blocked"),
        ],
        expect=[
            Expect("memory.read", "allow"),
            Expect("memory.read", "deny", "rate-limit-exceeded"),
        ],
    ),
    Scenario(
        name="capability-revoked-by-prior-use",
        why=(
            "WRITE_FS capability revoked_by={READ_FS}: once a READ_FS "
            "action has been dispatched in the session, the write is "
            "DENIED (tool-identity counterpart to the label rules)."
        ),
        caps=frozenset(
            {
                Capability(kind=K.READ_FS, pattern="*"),
                Capability(
                    kind=K.WRITE_FS,
                    pattern="*",
                    revoked_by=frozenset({K.READ_FS}),
                ),
            },
        ),
        pre=_seed,
        responses=[
            tool_turn("read first", tc("r", "memory.read", key="k")),
            tool_turn(
                "now write",
                tc("w", "memory.write", key="k2", value="v2"),
            ),
            final("write blocked"),
        ],
        expect=[
            Expect("memory.read", "allow"),
            Expect("memory.write", "deny", "capability-revoked-by-prior-use"),
        ],
    ),
]


async def main() -> int:
    return await run_suite(TITLE, SCENARIOS)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
