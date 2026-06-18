"""Compliance review workflow — FR-008 BLP top-up.

Workflow: a compliance officer reviews audit records that are
tier=RESTRICTED while operating under different profile ceilings.
The point: profile-bound clearance is the ceiling on what any
session can read, regardless of how broad the capabilities are.

Three profile configurations are run against the same audit
records to demonstrate the read-up refusal:

  - "auditor" profile (max_tier=RESTRICTED) → can read.
  - "external-consultant" (max_tier=SENSITIVE) → BLP refuses.
  - "intake" (max_tier=NONE) → refuses even at the lowest sensitive
    tier; this is the "fresh-hire" baseline.

Different storytelling from `hr_data_handling` (HR records) and
`clinical_records_research` (PHI). Here the records are audit /
compliance logs — operator-controlled but high-stakes.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.resolution import ContextProfile
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from demos.scenarios._helpers import (
    ai,
    audit,
    demo_header,
    make_app,
    make_session,
    note,
    policy_outcome,
    step,
    tool,
    user,
)


def _profiles() -> dict[str, ContextProfile]:
    return {
        "auditor": ContextProfile(
            id="auditor",
            user_pattern="*",
            use_case="compliance",
            max_tier=Tier.RESTRICTED,
        ),
        "external-consultant": ContextProfile(
            id="external-consultant",
            user_pattern="*",
            use_case="compliance",
            max_tier=Tier.SENSITIVE,
        ),
        "intake": ContextProfile(
            id="intake",
            user_pattern="*",
            use_case="general",
            max_tier=Tier.NONE,
        ),
    }


async def _read_under_profile(tmp_path: Any, profile_id: str) -> Any:
    ctx = PolicyContext(profiles=_profiles())
    app = make_app(tmp_path / profile_id, policy_context=ctx)
    await app.startup()
    # Pre-load the audit record with the proper tier expectation.
    personal_tag = CategoryTag("personal", Tier.REGULATED)
    app.memory.write(
        "audit-q1-findings",
        "Q1 compliance findings: 3 minor, 1 major. Mitigation pending.",
        LabelState(a=frozenset({personal_tag})),
    )
    s = await make_session(
        app,
        axis_a_categories=(("audit", Tier.RESTRICTED),),
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
    return await app.tool_client.call_tool(s.id, "memory.read", {"key": "audit-q1-findings"})


@pytest.mark.asyncio
async def test_compliance_review_workflow_demo(tmp_path: Any) -> None:
    demo_header(
        "Compliance Review — FR-008 BLP across three profile ceilings",
        blurb=(
            "Same audit record (tier=RESTRICTED) read under three "
            "profiles. Auditor reads. External consultant refuses "
            "(profile ceiling = SENSITIVE). Intake refuses (NONE). "
            "Demonstrates that the profile is the authority ceiling; "
            "capabilities don't override it."
        ),
        models=(
            "FR-008 Bell-LaPadula clearance",
            "profile-bound authority",
        ),
        patterns=("operator profile switch across compartments",),
    )

    step("Profile: auditor", "max_tier = RESTRICTED")
    user('"read the Q1 audit findings"')
    ai('call memory.read(key="audit-q1-findings")')
    ok = await _read_under_profile(tmp_path, "auditor")
    policy_outcome(ok)
    if ok.decision is Decision.ALLOW:
        excerpt = ok.output["value"][:50]
        tool(f'memory.read → "{excerpt}…"')
    assert ok.decision is Decision.ALLOW

    step("Profile: external-consultant", "max_tier = SENSITIVE — refuses")
    note(
        "External consultant has READ_FS capability for *, but the "
        "profile ceiling is SENSITIVE. The audit record is at "
        "RESTRICTED. BLP refuses the read-up."
    )
    ai('call memory.read(key="audit-q1-findings")')
    refused = await _read_under_profile(tmp_path, "external-consultant")
    policy_outcome(
        refused,
        rationale="FR-008: clearance=sensitive refuses read at tier=restricted.",
    )
    assert refused.decision is Decision.DENY

    step("Profile: intake", "max_tier = NONE — refuses everything sensitive")
    note(
        "A fresh-hire intake profile starts with no read-up authority "
        "at all. They can only see public-tier data. The audit record "
        "is invisible."
    )
    ai('call memory.read(key="audit-q1-findings")')
    intake_refused = await _read_under_profile(tmp_path, "intake")
    policy_outcome(
        intake_refused,
        rationale="FR-008: clearance=none refuses read at tier=restricted.",
    )
    assert intake_refused.decision is Decision.DENY

    audit(
        "Same caps, same data, three profiles, three outcomes. "
        "Authority is the profile ceiling; the cap set was necessary "
        "but not sufficient."
    )
