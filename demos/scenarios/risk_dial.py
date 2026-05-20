"""Risk dial — FR-030 / SC-010 envelope dial.

Story:
  The operator owns a `risk_preference` dial that selects a single
  outcome within each cell's declared {strictest, loosest} envelope.
  We run the SAME tool call against the SAME session three times,
  varying ONLY the dial. The outcomes track the dial.

  Cell envelope for (work, data.create_local, principal:alice,
  reversible):
    strictest = REQUIRE_APPROVAL
    loosest   = AUTO

    Dial = cautious   → REQUIRE_APPROVAL
    Dial = balanced   → SUGGEST  → ALLOW (legacy compose)
    Dial = permissive → AUTO     → ALLOW

  We use memory.create because its declared (reversible, system)
  gives AUTO_OK at the reversibility gate, so the envelope dial is
  the only thing steering the outcome.

Security models exercised:
  - FR-030 outcome envelopes
  - SC-010 invariant: dial never crosses the hard floor (covered by a
    second cell that is a HARD-FLOOR envelope — the dial cannot move
    it regardless of value)
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import RuleOutcome
from capabledeputy.policy.envelope import (
    CellKey,
    EnvelopeSet,
    OutcomeEnvelope,
    RiskPreference,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.client import PolicyContext
from demos.scenarios._helpers import make_app, make_session, narrate


def _make_envelope_set() -> EnvelopeSet:
    """Two cells:
    1. movable: data.write_local — strictest=REQUIRE_APPROVAL,
       loosest=AUTO. Dial steers within these.
    2. hard-floor: social.send_email — strictest=loosest=DENY. The
       dial cannot move it; the cell is operator-locked.
    """
    movable = OutcomeEnvelope(
        cell=CellKey(
            category="work",
            effect="data.create_local",
            decision_context_canonical="principal:alice",
            reversibility="reversible",
        ),
        strictest=RuleOutcome.REQUIRE_APPROVAL,
        loosest=RuleOutcome.AUTO,
    )
    hard_floor = OutcomeEnvelope(
        cell=CellKey(
            category="work",
            effect="social.send_email",
            decision_context_canonical="principal:alice",
            reversibility="irreversible",
        ),
        strictest=RuleOutcome.DENY,
        loosest=RuleOutcome.DENY,
    )
    return EnvelopeSet(by_cell={movable.cell: movable, hard_floor.cell: hard_floor})


async def _run_at_dial(tmp_path: Any, dial: RiskPreference) -> tuple[Decision, str | None]:
    ctx = PolicyContext(
        envelope_set=_make_envelope_set(),
        risk_preference=dial,
    )
    app = make_app(tmp_path / dial.value, policy_context=ctx)
    await app.startup()
    s = await make_session(
        app,
        axis_a_categories=(("work", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.CREATE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )
    outcome = await app.tool_client.call_tool(
        s.id,
        "memory.create",
        {"key": "k", "value": "v"},
    )
    return outcome.decision, outcome.rule


@pytest.mark.asyncio
async def test_risk_dial_demo(tmp_path: Any) -> None:
    narrate(
        "Risk Dial — operator-owned autonomy dial",
        """
        Same tool call. Three dial values. Three outcomes. The
        envelope's strictest is always honored (operator-locked floor).
        The dial only steers within the declared envelope.
        """,
    )

    for dial in (RiskPreference.CAUTIOUS, RiskPreference.BALANCED, RiskPreference.PERMISSIVE):
        decision, rule = await _run_at_dial(tmp_path, dial)
        narrate(
            f"Dial = {dial.value}",
            f"memory.create → decision={decision.value}  rule={rule}",
        )

    # Cautious + balanced should both ratchet to REQUIRE_APPROVAL
    # (cautious picks strictest; balanced midpoint of 4 outcomes between
    # REQUIRE_APPROVAL and AUTO rounds toward stricter).
    cautious_decision, cautious_rule = await _run_at_dial(
        tmp_path / "verify-c",
        RiskPreference.CAUTIOUS,
    )
    assert cautious_decision is Decision.REQUIRE_APPROVAL
    assert cautious_rule is not None and "envelope-dial" in cautious_rule

    permissive_decision, _ = await _run_at_dial(
        tmp_path / "verify-p",
        RiskPreference.PERMISSIVE,
    )
    assert permissive_decision is Decision.ALLOW

    narrate(
        "Hard-floor cell",
        """
        social.send_email cell has strictest == loosest == DENY.
        SC-010 invariant: even on permissive, the dial cannot move it.
        """,
    )
    # Re-run social.send_email at PERMISSIVE — should still DENY.
    ctx = PolicyContext(
        envelope_set=_make_envelope_set(),
        risk_preference=RiskPreference.PERMISSIVE,
    )
    app = make_app(tmp_path / "hard-floor", policy_context=ctx)
    await app.startup()
    s = await make_session(
        app,
        axis_a_categories=(("work", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*@example.com",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )
    out = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "x", "body": "y"},
    )
    assert out.decision is Decision.DENY
    narrate(
        "  → hard-floor result",
        f"email.send at PERMISSIVE → {out.decision.value} (rule={out.rule}).\n"
        "    Floor held. SC-010 verified.",
    )
