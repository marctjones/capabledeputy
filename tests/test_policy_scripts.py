"""CI gate for the deterministic policy harness scripts.

The runnable suites live in scripts/policy_*.py (each independently
executable for local inspection). This test imports their SCENARIOS and
runs every one through the shared harness so the full allow/deny/
require-approval/constraint/label matrix is enforced in the normal
pytest run — no real LLM, no network, fully deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import policy_allow  # noqa: E402
import policy_constraints  # noqa: E402
import policy_deny  # noqa: E402
import policy_engine_harness  # noqa: E402
import policy_labels  # noqa: E402
import policy_require_approval  # noqa: E402
import policy_workflows  # noqa: E402
from _policy_harness import Scenario, run_scenario  # noqa: E402

_SUITES = (
    policy_engine_harness,
    policy_allow,
    policy_deny,
    policy_require_approval,
    policy_constraints,
    policy_labels,
    policy_workflows,
)

_CASES: list[tuple[str, Scenario]] = [(mod.__name__, sc) for mod in _SUITES for sc in mod.SCENARIOS]


def test_every_suite_has_scenarios() -> None:
    """Guard against an import/refactor silently emptying a suite."""
    for mod in _SUITES:
        assert mod.SCENARIOS, f"{mod.__name__} has no scenarios"
    assert len(_CASES) >= 28


@pytest.mark.parametrize(
    ("suite", "scenario"),
    _CASES,
    ids=[f"{mod}:{sc.name}" for mod, sc in _CASES],
)
async def test_policy_scenario(suite: str, scenario: Scenario) -> None:
    failures = await run_scenario(scenario)
    assert not failures, f"{suite}:{scenario.name}\n" + "\n".join(failures)
