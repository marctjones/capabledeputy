"""Driver for the full demo suite.

Run with:
    uv run pytest demos/scenarios/run_all.py --no-cov -v -s

The -s is what makes the narration visible. Each demo prints its own
operator-facing storyline; this driver invokes them in the canonical
order that the spec-006 walkthrough uses.
"""

from __future__ import annotations

from typing import Any

import pytest

from demos.scenarios.bulk_approval_grouped import test_bulk_approval_demo
from demos.scenarios.clinical_records_research import test_clinical_records_demo
from demos.scenarios.daily_briefing import test_daily_briefing_demo
from demos.scenarios.data_blind_disclosure import test_data_blind_disclosure_demo
from demos.scenarios.hr_data_handling import test_hr_data_handling_demo
from demos.scenarios.optimistic_burn import test_optimistic_burn_demo
from demos.scenarios.override_workflow import test_override_workflow_demo
from demos.scenarios.prompt_injection_defense import test_prompt_injection_demo
from demos.scenarios.risk_dial import test_risk_dial_demo

# Order: opens with the marquee (daily briefing), then the FSM-heavy
# override workflow, then the operator-knob demos (risk dial,
# clinical, HR), then defense-in-depth demos (prompt injection,
# optimistic, bulk approval), and closes with the structural
# data-blind disclosure pattern.
_RUN_ORDER: tuple[tuple[str, Any], ...] = (
    ("Daily Briefing", test_daily_briefing_demo),
    ("Override Workflow", test_override_workflow_demo),
    ("Risk Dial", test_risk_dial_demo),
    ("Clinical Records", test_clinical_records_demo),
    ("HR Data Handling", test_hr_data_handling_demo),
    ("Prompt Injection Defense", test_prompt_injection_demo),
    ("Optimistic Burn", test_optimistic_burn_demo),
    ("Bulk Approval Grouped", test_bulk_approval_demo),
    ("Data-Blind Disclosure", test_data_blind_disclosure_demo),
)


@pytest.mark.asyncio
async def test_run_all_demos(tmp_path: Any) -> None:
    """Run every demo in order. Each gets its own subdirectory so the
    audit logs and state DBs don't collide."""
    for title, demo in _RUN_ORDER:
        print("\n" + "=" * 70)
        print(f"  DEMO: {title}")
        print("=" * 70)
        sub = tmp_path / title.lower().replace(" ", "_").replace("-", "_")
        sub.mkdir(parents=True, exist_ok=True)
        await demo(sub)
