"""Dial-assisted research — envelope dial + multi-fetch web research.

Same multi-step workflow run under three dial settings:
  1. Fetch 3 web pages (each tagged UNTRUSTED_EXTERNAL)
  2. Save a research report to memory (data.create_local cell)
  3. Verify a hard-floor egress (email the report) still refuses

The envelope cell for (research, data.create_local, principal:alice,
reversible) is movable from REQUIRE_APPROVAL → AUTO. The dial steers
within that envelope. The email send is in a hard-floor cell so the
dial cannot move it — SC-010 holds.

This is the canonical "operator dial" demo: same code, same data,
the autonomy boundary slides on operator intent without rewriting
any rule.
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


def _envelope_set() -> EnvelopeSet:
    save_cell = OutcomeEnvelope(
        cell=CellKey(
            category="research",
            effect="data.create_local",
            decision_context_canonical="principal:alice",
            reversibility="reversible",
        ),
        strictest=RuleOutcome.REQUIRE_APPROVAL,
        loosest=RuleOutcome.AUTO,
    )
    egress_cell = OutcomeEnvelope(
        cell=CellKey(
            category="research",
            effect="social.send_email",
            decision_context_canonical="principal:alice",
            reversibility="irreversible",
        ),
        strictest=RuleOutcome.DENY,
        loosest=RuleOutcome.DENY,
    )
    return EnvelopeSet(by_cell={save_cell.cell: save_cell, egress_cell.cell: egress_cell})


async def _research_run(tmp_path: Any, dial: RiskPreference) -> tuple[Any, Any]:
    ctx = PolicyContext(envelope_set=_envelope_set(), risk_preference=dial)
    app = make_app(tmp_path / dial.value, policy_context=ctx)
    await app.startup()

    # Pre-load 3 fixture pages.
    app.web.serve("https://a.example/news", "Quarterly update: revenue up 12%.")
    app.web.serve("https://b.example/blog", "Industry analysis: spend on AI rising.")
    app.web.serve("https://c.example/wiki", "Background reading: ML history.")

    s = await make_session(
        app,
        axis_a_categories=(("research", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.WEB_FETCH,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.CREATE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*@example.com",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    # Fetches: each tags the session with UNTRUSTED_EXTERNAL.
    for url in (
        "https://a.example/news",
        "https://b.example/blog",
        "https://c.example/wiki",
    ):
        await app.tool_client.call_tool(s.id, "web.fetch", {"url": url})

    # Step under test: save the report. Decision steered by the dial.
    save = await app.tool_client.call_tool(
        s.id,
        "memory.create",
        {"key": "research-report", "value": "synthesized report (stub)"},
    )

    # Hard-floor counter-factual: email the report.
    egress = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "boss@example.com", "subject": "Report", "body": "(see attached)"},
    )

    return save, egress


@pytest.mark.asyncio
async def test_dial_assisted_research_demo(tmp_path: Any) -> None:
    demo_header(
        "Dial-Assisted Research — envelope dial across a real workflow",
        blurb=(
            "Three web fetches + one report save + one hard-floor "
            "counter-factual, run under three dial values. The save's "
            "decision moves with the dial; the egress holds at DENY "
            "regardless (SC-010 hard floor)."
        ),
        models=("FR-030 envelope dial", "SC-010 hard-floor invariant"),
        patterns=("multi-fetch UNTRUSTED_EXTERNAL accumulation",),
    )

    for dial in (
        RiskPreference.CAUTIOUS,
        RiskPreference.BALANCED,
        RiskPreference.PERMISSIVE,
    ):
        step(f"Dial = {dial.value}", "fetch 3 pages → save report → attempt egress")
        ai("web.fetch x 3  →  memory.create('research-report', …)  →  email.send")
        save, egress = await _research_run(tmp_path, dial)
        policy_outcome(
            save,
            rationale="data.create_local cell — dial moves this outcome.",
        )
        if save.decision is Decision.ALLOW:
            tool("memory.create → ok")
        else:
            tool("(save deferred — operator review)")
        policy_outcome(
            egress,
            rationale=(
                "social.send_email is a hard-floor cell. SC-010: dial "
                "cannot move it regardless of value."
            ),
        )
        tool("(email skipped)")

    # Hard assertions: cautious denies save, permissive allows; both
    # deny the egress.
    cautious_save, cautious_egress = await _research_run(
        tmp_path / "verify-c",
        RiskPreference.CAUTIOUS,
    )
    permissive_save, permissive_egress = await _research_run(
        tmp_path / "verify-p",
        RiskPreference.PERMISSIVE,
    )
    assert cautious_save.decision is Decision.REQUIRE_APPROVAL
    assert permissive_save.decision is Decision.ALLOW
    assert cautious_egress.decision is Decision.DENY
    assert permissive_egress.decision is Decision.DENY

    note(
        "Save outcome tracked the dial across three settings; egress held "
        "at DENY across all three. The dial is operator autonomy; the "
        "hard floor is operator-locked policy."
    )
