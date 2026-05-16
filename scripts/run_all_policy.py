#!/usr/bin/env python
"""Run every deterministic policy suite and aggregate the result.

Imports the SCENARIOS/TITLE from each themed scripts/policy_*.py and
runs them in one process. Exit 0 iff every suite passes.

Run:  uv run python scripts/run_all_policy.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import policy_allow
import policy_constraints
import policy_deny
import policy_engine_harness
import policy_labels
import policy_require_approval
from _policy_harness import run_suite

SUITES = [
    policy_engine_harness,
    policy_allow,
    policy_deny,
    policy_require_approval,
    policy_constraints,
    policy_labels,
]


async def main() -> int:
    rc = 0
    scenario_total = 0
    for mod in SUITES:
        rc |= await run_suite(mod.TITLE, mod.SCENARIOS)
        scenario_total += len(mod.SCENARIOS)
    print("\n" + "#" * 68)
    if rc:
        print(f"OVERALL: FAIL — one or more of {len(SUITES)} suites had mismatches")
    else:
        print(
            f"OVERALL: PASS — {len(SUITES)} suites, "
            f"{scenario_total} scenarios, all decisions as scripted",
        )
    print("#" * 68)
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
