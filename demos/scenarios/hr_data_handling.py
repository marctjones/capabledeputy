"""HR data handling — clearance profile-driven authority.

Story:
  Two profiles, same tool call, same data, opposite outcomes:

    - "hr-analyst" profile (max_tier=RESTRICTED): may read HR
      records at tier RESTRICTED. ALLOW.
    - "intern" profile     (max_tier=SENSITIVE):  cannot read at
      RESTRICTED. BLP refuses.

  Authority is profile-bound, not user-bound. Switching profiles
  (`capdep profile switch` at the CLI level) changes the ceiling
  without changing the caps. This is the moral inverse of "give
  the agent every capability and pray" — the *profile* enforces
  the read-up ceiling regardless of what caps the agent acquired.

Security models exercised:
  - FR-008 Bell-LaPadula clearance
  - Profile-bound authority (profile is the ceiling, not the cap set)
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
from capabledeputy.tools.client import PolicyContext
from demos.scenarios._helpers import make_app, make_session, narrate


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


async def _read_hr_under_profile(tmp_path: Any, profile_id: str) -> tuple[Decision, str | None]:
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
    out = await app.tool_client.call_tool(s.id, "memory.read", {"key": "salary"})
    return out.decision, out.rule


@pytest.mark.asyncio
async def test_hr_data_handling_demo(tmp_path: Any) -> None:
    narrate(
        "HR Data Handling — profile-bound clearance",
        """
        Same caps. Same data. Different profile. Different outcome.
        Authority is the profile ceiling; the cap set is *necessary*,
        not sufficient.
        """,
    )

    # hr-analyst can read RESTRICTED HR records.
    ok, ok_rule = await _read_hr_under_profile(tmp_path, "hr-analyst")
    narrate("hr-analyst", f"memory.read → {ok.value} (rule={ok_rule})")
    assert ok is Decision.ALLOW

    # intern cannot read at RESTRICTED; BLP refuses regardless of cap.
    refused, refused_rule = await _read_hr_under_profile(tmp_path, "intern")
    narrate(
        "intern",
        f"memory.read → {refused.value} (rule={refused_rule})\n"
        "    Same cap. BLP refuses at the profile ceiling.",
    )
    assert refused is Decision.DENY
