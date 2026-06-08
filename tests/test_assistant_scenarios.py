"""The personal-assistant scenario catalogue (~1000+ cases).

Runs every scenario in scripts/policy_assistant.py through the real agent
loop + real policy engine, asserting the correct allow / deny /
require-approval outcome for each. FakeLLM + in-memory native tools only —
no real LLM, no network, NO real email / purchase / calendar side effects.

Marked `slow`: the default fast run (`-m "not slow"`) skips it; full CI and
`uv run python scripts/policy_assistant.py` exercise the whole catalogue.
The representative subset runs in the fast suite via test_policy_scripts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import policy_assistant
from _policy_harness import run_scenario


def test_catalogue_is_large() -> None:
    assert len(policy_assistant.SCENARIOS) >= 1000, (
        f"expected >=1000 scenarios, got {len(policy_assistant.SCENARIOS)}"
    )
    # No duplicate scenario names (each is a distinct, named use case).
    names = [s.name for s in policy_assistant.SCENARIOS]
    assert len(names) == len(set(names)), "duplicate scenario names"


@pytest.mark.slow
async def test_assistant_scenario_catalogue() -> None:
    failures: list[str] = []
    for sc in policy_assistant.SCENARIOS:
        fs = await run_scenario(sc)
        if fs:
            failures.append(f"{sc.name}: {fs}")
    assert not failures, (
        f"{len(failures)}/{len(policy_assistant.SCENARIOS)} scenario(s) failed:\n"
        + "\n".join(failures[:25])
    )
