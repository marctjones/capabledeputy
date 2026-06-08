"""CI guard for the narrated morning-assistant demo.

demos/scenarios/ is not in `testpaths`, so demos don't run in the default
suite — which is how several demos silently broke after the label-model
redesign. This runs the (repaired, asserting) morning-assistant demo so it
stays green. Its own asserts verify the allow/deny/approval outcomes.
"""

from __future__ import annotations

from demos.scenarios.morning_assistant import test_morning_assistant as _demo


async def test_morning_assistant_demo_runs(tmp_path) -> None:
    await _demo(tmp_path)
