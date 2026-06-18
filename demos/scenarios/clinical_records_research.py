"""Clinical records research — Brewer-Nash + BLP read-up refusal.

Two contrasting sub-sessions:
  A. researcher who already touched PHI cannot then email (Brewer-Nash)
  B. low-clearance profile cannot read regulated records at all (BLP)
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
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
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


@pytest.mark.asyncio
async def test_clinical_records_demo(tmp_path: Any) -> None:
    demo_header(
        "Clinical Records — Brewer-Nash + BLP",
        blurb=(
            "Two contrasting sub-sessions. A: PHI taint sticks and blocks "
            "egress. B: low clearance profile refuses the read at the "
            "first step."
        ),
        models=("Brewer-Nash conflict rules", "FR-008 Bell-LaPadula"),
        patterns=("legacy v0.7 label propagation",),
    )

    step("Part A", "Researcher session already tagged CONFIDENTIAL_HEALTH")
    note("In a real flow the label arrives via a memory.read on a PHI record.")
    app = make_app(tmp_path / "a", policy_context=PolicyContext())
    await app.startup()
    s = await make_session(
        app,
        axis_a_categories=(("health", Tier.RESTRICTED),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*@hospital.org",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    ai('call email.send(to="team@hospital.org", …)')
    out = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "team@hospital.org", "subject": "Notes", "body": "Patient..."},
    )
    assert out.decision is Decision.DENY
    policy_outcome(
        out,
        rationale=(
            "Brewer-Nash: confidential.health + egress.email is a flat "
            "deny. The agent cannot launder PHI through email."
        ),
    )
    tool("(skipped)")

    step("Part B", "Low-clearance profile tries to read regulated category")
    note(
        "PolicyContext.clearance_max_tier = NONE; session category is "
        "REGULATED. BLP gate fires before any tool reaches the store."
    )
    blp_ctx = PolicyContext(clearance_max_tier=Tier.NONE)
    blp_app = make_app(tmp_path / "b", policy_context=blp_ctx)
    await blp_app.startup()
    s2 = await make_session(
        blp_app,
        axis_a_categories=(("health", Tier.RESTRICTED),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.READ_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )
    ai('call memory.read(key="patient-12")')
    out2 = await blp_app.tool_client.call_tool(s2.id, "memory.read", {"key": "patient-12"})
    assert out2.decision is Decision.DENY
    policy_outcome(
        out2,
        rationale=(
            "FR-008: clearance=none refuses read at tier=regulated. The "
            "cap says ALLOW; the BLP floor overrides."
        ),
    )
    tool("(skipped)")
