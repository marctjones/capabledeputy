"""HR data handling — clearance profile-driven authority.

Same caps. Same data. Different profile. Different outcome. Authority
is the profile ceiling; the cap set is necessary, not sufficient.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.resolution import ContextProfile
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.policy.context import PolicyContext
from demos.scenarios._helpers import (
    ai,
    demo_header,
    make_app,
    make_session,
    note,
    policy_outcome,
    step,
    tool,
)


def _profiles() -> dict[str, ContextProfile]:
    return {
        "hr-analyst": ContextProfile(
            id="hr-analyst",
            user_pattern="*",
            use_case="hr-review",
            max_tier=Tier.RESTRICTED,
        ),
        "intern": ContextProfile(
            id="intern",
            user_pattern="*",
            use_case="general",
            max_tier=Tier.SENSITIVE,
        ),
    }


async def _read_hr_under_profile(tmp_path: Any, profile_id: str) -> Any:
    ctx = PolicyContext(profiles=_profiles())
    app = make_app(tmp_path / profile_id, policy_context=ctx)
    await app.startup()
    s = await make_session(
        app,
        axis_a_categories=(("hr", Tier.RESTRICTED),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.READ_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
        clearance_profile_id=profile_id,
    )
    return await app.tool_client.call_tool(s.id, "memory.read", {"key": "salary"})


@pytest.mark.asyncio
async def test_hr_data_handling_demo(tmp_path: Any) -> None:
    demo_header(
        "HR Data Handling — profile-bound clearance",
        blurb=(
            "Same caps, same data, different profile. Authority is the "
            "profile ceiling; the cap set is necessary, not sufficient."
        ),
        models=("FR-008 BLP clearance", "profile-bound authority"),
        patterns=("operator profile switch",),
    )

    step("Profile: hr-analyst", "max_tier = RESTRICTED")
    ai('call memory.read(key="salary")')
    ok = await _read_hr_under_profile(tmp_path, "hr-analyst")
    policy_outcome(ok)
    if ok.decision is Decision.ALLOW:
        tool("memory.read → ok")
    assert ok.decision is Decision.ALLOW

    step("Profile: intern", "max_tier = SENSITIVE")
    note("Same caps. Same data. Profile ceiling refuses the read-up.")
    ai('call memory.read(key="salary")')
    refused = await _read_hr_under_profile(tmp_path, "intern")
    policy_outcome(
        refused,
        rationale="FR-008: clearance=sensitive refuses read at tier=restricted.",
    )
    assert refused.decision is Decision.DENY
