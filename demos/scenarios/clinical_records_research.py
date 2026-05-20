"""Clinical records research — Brewer-Nash + BLP read-up refusal.

Story:
  Two contrasting sub-sessions illustrate the security model:

  A. A researcher session that already touched protected health
     records (label CONFIDENTIAL_HEALTH on its label_set) tries to
     send an email. Brewer-Nash rule `health-meets-egress` fires and
     DENIES. The system carries data-class context FORWARD: the agent
     cannot "forget" that it has read sensitive data and egress it.

  B. A second session bound to a clearance profile with max_tier=PUBLIC
     tries to read regulated clinical records. FR-008 BLP refuses the
     read-up attempt regardless of whatever capability the session
     holds. Clearance is a hard floor — capabilities cannot exceed it.

Security models exercised:
  - Brewer-Nash conflict rules (legacy v0.7 surface still active)
  - FR-008 Bell-LaPadula read-up refusal
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.client import PolicyContext
from demos.scenarios._helpers import make_app, make_session, narrate


@pytest.mark.asyncio
async def test_clinical_records_demo(tmp_path: Any) -> None:
    narrate(
        "Clinical Records — Brewer-Nash + BLP",
        """
        Sub-session A: a researcher who already read PHI cannot then
        email a teammate — the label sticks to the session.
        Sub-session B: a low-clearance profile cannot read regulated
        records at all — BLP refuses the read-up before any tool runs.
        """,
    )

    # ----- Sub-session A: Brewer-Nash --------------------------------
    narrate("Part A", "Researcher accumulated CONFIDENTIAL_HEALTH on session.")
    app = make_app(tmp_path / "a", policy_context=PolicyContext())
    await app.startup()
    s = await make_session(
        app,
        axis_a_categories=(("clinical", Tier.REGULATED),),
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
    # Researcher already read PHI; this would normally land via a
    # tool's inherent_labels. We inject it directly for the demo.
    await app.graph.add_labels(s.id, frozenset({Label.CONFIDENTIAL_HEALTH}))

    out = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "team@hospital.org", "subject": "Notes", "body": "Patient..."},
    )
    assert out.decision is Decision.DENY
    assert out.rule in {"health-meets-egress", None}  # legacy or v2 rule
    narrate(
        "  → result",
        f"email.send → {out.decision.value} (rule={out.rule})\n"
        "    Brewer-Nash: confidential.health + egress.email is a flat\n"
        "    deny. The agent cannot launder PHI through email.",
    )

    # ----- Sub-session B: BLP ----------------------------------------
    narrate("Part B", "Low-clearance profile tries to read a regulated category.")
    blp_ctx = PolicyContext(clearance_max_tier=Tier.NONE)
    blp_app = make_app(tmp_path / "b", policy_context=blp_ctx)
    await blp_app.startup()
    s2 = await make_session(
        blp_app,
        axis_a_categories=(("clinical", Tier.REGULATED),),
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
    # Use a tool that declares an effect_class so the v2 leg fires
    # and the BLP gate is consulted.
    out2 = await blp_app.tool_client.call_tool(
        s2.id,
        "memory.read",
        {"key": "patient-12"},
    )
    assert out2.decision is Decision.DENY
    narrate(
        "  → result",
        f"memory.read → {out2.decision.value} (rule={out2.rule})\n"
        "    FR-008: clearance=none refuses read at tier=regulated.\n"
        "    The capability says ALLOW; the BLP floor overrides.",
    )
