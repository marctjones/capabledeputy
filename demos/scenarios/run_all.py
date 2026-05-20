"""Driver for the full demo suite.

Run with:
    uv run pytest demos/scenarios/run_all.py --no-cov -v -s

The -s is what makes the narration visible. The driver invokes each
demo with a 'DEMO i / N' banner so a long run is easy to scan.

The 12 demos are organized in three arcs:

  Single-mechanism demos
    Each exercises one security model or flow pattern cleanly so the
    mechanism is visible in isolation.

  Multi-mechanism demos
    Each combines 3+ mechanisms in a realistic workflow. These are
    the load-bearing arguments for the design — security models
    that compose are stronger than any single layer.

  Structural-invariant demos
    Demos that prove a property holds by construction (monotone
    inheritance, single-use grants, hard floors).
"""

from __future__ import annotations

from typing import Any

import pytest

from demos.scenarios.bulk_approval_grouped import test_bulk_approval_demo
from demos.scenarios.clinical_records_research import test_clinical_records_demo
from demos.scenarios.daily_briefing import test_daily_briefing_demo
from demos.scenarios.data_blind_disclosure import test_data_blind_disclosure_demo
from demos.scenarios.dial_assisted_research import test_dial_assisted_research_demo
from demos.scenarios.hr_data_handling import test_hr_data_handling_demo
from demos.scenarios.multi_session_handoff import test_multi_session_handoff_demo
from demos.scenarios.optimistic_burn import test_optimistic_burn_demo
from demos.scenarios.override_workflow import test_override_workflow_demo
from demos.scenarios.prompt_injection_defense import test_prompt_injection_demo
from demos.scenarios.risk_dial import test_risk_dial_demo
from demos.scenarios.secure_inbox_triage import test_secure_inbox_triage_demo

# Order:
#   1-3   Single mechanisms, easy intro (briefing → override → risk dial)
#   4-7   Operator-knob + clearance demos
#   8     Inbox triage — the canonical Pattern ② + ③ + inspector mix
#   9     Multi-session handoff — fork inheritance
#   10    Dial-assisted research — dial steering a real workflow
#   11    Bulk approval — programmatic execution + bundle
#   12    Data-blind disclosure — Pattern ③ structural test

_RUN_ORDER: tuple[tuple[str, Any], ...] = (
    ("Daily Briefing", test_daily_briefing_demo),
    ("Override Workflow", test_override_workflow_demo),
    ("Risk Dial", test_risk_dial_demo),
    ("Clinical Records", test_clinical_records_demo),
    ("HR Data Handling", test_hr_data_handling_demo),
    ("Prompt Injection Defense", test_prompt_injection_demo),
    ("Optimistic Burn", test_optimistic_burn_demo),
    ("Secure Inbox Triage", test_secure_inbox_triage_demo),
    ("Multi-Session Handoff", test_multi_session_handoff_demo),
    ("Dial-Assisted Research", test_dial_assisted_research_demo),
    ("Bulk Approval Grouped", test_bulk_approval_demo),
    ("Data-Blind Disclosure", test_data_blind_disclosure_demo),
)


@pytest.mark.asyncio
async def test_run_all_demos(tmp_path: Any) -> None:
    """Run every demo in order. Each demo's `demo_header()` call
    already paints the top banner, so the driver doesn't need to."""
    n_of = len(_RUN_ORDER)
    for idx, (title, demo) in enumerate(_RUN_ORDER, start=1):
        print(f"\n\n>>> Running demo {idx} / {n_of}: {title}")
        sub = tmp_path / title.lower().replace(" ", "_").replace("-", "_")
        sub.mkdir(parents=True, exist_ok=True)
        await demo(sub)
