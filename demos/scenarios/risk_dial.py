"""Risk dial — FR-030 / SC-010 envelope dial.

Same tool call. Three dial values. Three outcomes. Plus a hard-floor
cell that the dial cannot move (SC-010).
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


def _make_envelope_set() -> EnvelopeSet:
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


async def _run_at_dial(tmp_path: Any, dial: RiskPreference) -> Any:
    ctx = PolicyContext(envelope_set=_make_envelope_set(), risk_preference=dial)
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
    return await app.tool_client.call_tool(
        s.id,
        "memory.create",
        {"key": "k", "value": "v"},
    )


@pytest.mark.asyncio
async def test_risk_dial_demo(tmp_path: Any) -> None:
    demo_header(
        "Risk Dial — operator-owned autonomy dial",
        blurb=(
            "Same call, three dial settings, three outcomes. The dial "
            "steers within the cell envelope but never crosses a hard "
            "floor (SC-010)."
        ),
        models=("FR-030 envelope dial", "SC-010 hard-floor invariant"),
        patterns=("operator-curated autonomy boundary",),
    )

    for dial in (
        RiskPreference.CAUTIOUS,
        RiskPreference.BALANCED,
        RiskPreference.PERMISSIVE,
    ):
        step(f"Dial = {dial.value}", "memory.create on a reversible/system effect")
        ai('call memory.create(key="k", value="v")')
        out = await _run_at_dial(tmp_path, dial)
        policy_outcome(out)
        if out.decision is Decision.ALLOW:
            tool("memory.create → ok")
        else:
            tool("(skipped)")

    cautious = await _run_at_dial(tmp_path / "verify-c", RiskPreference.CAUTIOUS)
    assert cautious.decision is Decision.REQUIRE_APPROVAL
    assert cautious.rule is not None and "envelope-dial" in cautious.rule

    permissive = await _run_at_dial(tmp_path / "verify-p", RiskPreference.PERMISSIVE)
    assert permissive.decision is Decision.ALLOW

    step("Hard-floor cell", "social.send_email at PERMISSIVE")
    note(
        "social.send_email cell has strictest == loosest == DENY. "
        "Even on permissive, the dial cannot move it (SC-010)."
    )
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
    ai('call email.send(to="bob@example.com", …)')
    out = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "x", "body": "y"},
    )
    assert out.decision is Decision.DENY
    policy_outcome(out, rationale="Floor held. SC-010 verified.")
