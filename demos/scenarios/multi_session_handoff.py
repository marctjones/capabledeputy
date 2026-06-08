"""Multi-session handoff — label propagation across session.fork().

Real workflow: a clinician's session reads a patient's record (PHI),
then forks a child session to draft a teammate-summary email. The
child INHERITS the parent's labels — the design choice that makes
"spawn a fresh agent and trick it" structurally fail: taint travels
along the fork.

Two paths shown:
  Path A — child session inherits CONFIDENTIAL_HEALTH; the send
           refuses (Brewer-Nash) without bypass.
  Path B — operator instead spawns a FRESH (top-level) session that
           never read the PHI; that session can send. The point is
           operator-explicit re-scoping is the legitimate way; child
           sessions cannot launder taint by accident.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.client import PolicyContext
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


@pytest.mark.asyncio
async def test_multi_session_handoff_demo(tmp_path: Any) -> None:
    demo_header(
        "Multi-Session Handoff — taint travels along fork",
        blurb=(
            "Parent reads PHI. Forks a child to draft a summary. Child "
            "inherits the label_set — the send refuses. Operator can "
            "re-scope by spawning a fresh top-level session, not by "
            "laundering through a child."
        ),
        models=("Brewer-Nash health-meets-egress", "FR-013 fork inheritance"),
        patterns=("session fork", "operator re-scope via fresh session"),
    )

    app = make_app(tmp_path, policy_context=PolicyContext())
    await app.startup()

    # Pre-seed the memory store with a PHI record so memory.read
    # propagates a real CONFIDENTIAL_HEALTH label onto the parent.
    app.memory.write(
        "patient-12",
        "BP 130/80, glucose normal.",
        LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)})),
    )

    parent = await make_session(
        app,
        axis_a_categories=(("clinical", Tier.REGULATED),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.READ_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*@hospital.org",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    step(1, "Parent session reads the patient's record")
    user('"summarize patient-12 for the care team"')
    ai('call memory.read(key="patient-12")')
    read_out = await app.tool_client.call_tool(parent.id, "memory.read", {"key": "patient-12"})
    assert read_out.decision is Decision.ALLOW
    policy_outcome(read_out)
    parent_after = app.graph.get(parent.id)
    note(f"parent.label_state now: {sorted(tag.category for tag in parent_after.label_state.a)}")
    assert any(tag.category == "health" for tag in parent_after.label_state.a)
    tool("memory.read → ok; PHI label propagated.")

    step(2, "Path A — fork a child session to draft a teammate email")
    user("graph.fork(parent) — spawn child to compose summary")
    child = await app.graph.fork(parent.id, intent="draft summary email")
    audit(
        f"fork emitted SESSION_FORKED. child={child.id} inherits "
        f"label_state + capability_set from parent."
    )
    note(
        f"child.label_state: {sorted(tag.category for tag in child.label_state.a)} — "
        "health traveled with the fork."
    )
    assert any(tag.category == "health" for tag in child.label_state.a)

    step(3, "Child attempts to email the summary")
    ai('call email.send(to="team@hospital.org", …) — from CHILD session')
    child_send = await app.tool_client.call_tool(
        child.id,
        "email.send",
        {"to": "team@hospital.org", "subject": "Patient 12", "body": "Summary..."},
    )
    assert child_send.decision is Decision.DENY
    policy_outcome(
        child_send,
        rationale=(
            "Brewer-Nash: child's inherited CONFIDENTIAL_HEALTH meets "
            "egress.email. The fork did NOT let the agent escape the "
            "parent's information-flow constraint."
        ),
    )
    tool("(skipped — child cannot launder PHI through a forked sub-agent)")

    step(4, "Path B — operator spawns a FRESH top-level session")
    note(
        "Fresh sessions have no PHI label. They can send — but they "
        "also have no access to the PHI without re-reading it, which "
        "would re-taint them. Re-scoping is operator-explicit."
    )
    user("graph.new() — fresh session for outbound summary only")
    fresh = await make_session(
        app,
        axis_a_categories=(("clinical-summary", Tier.SENSITIVE),),
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
    note(
        f"fresh.label_state.a: {sorted(tag.category for tag in fresh.label_state.a) or '[]'} "
        "— clean by construction."
    )
    assert not any(tag.category == "health" for tag in fresh.label_state.a)

    ai('call email.send(to="team@hospital.org", …) — from FRESH session')
    fresh_send = await app.tool_client.call_tool(
        fresh.id,
        "email.send",
        {
            "to": "team@hospital.org",
            "subject": "Care plan summary",
            "body": "Pre-approved summary, no PHI.",
        },
    )
    # The fresh session still trips FR-019 social-commitment refusal —
    # email.send is irreversible. This demo's point is structural taint
    # inheritance, not whether email goes through.
    policy_outcome(
        fresh_send,
        rationale=(
            "Fresh session avoids the PHI-meets-egress refusal. The "
            "remaining FR-019 social-commitment refusal is the SAME one "
            "every send hits — the operator would override that the "
            "normal way."
        ),
    )
    note(
        "Structural point: fork carried PHI taint into the child by "
        "design; only an explicit fresh session sidesteps it, and the "
        "operator's signature is on that choice."
    )
