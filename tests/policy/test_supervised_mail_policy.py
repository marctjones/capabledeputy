"""The supervised mail path must not need broad Starlark relaxations."""

from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.decision_inspector_loader import load_decision_inspectors
from capabledeputy.policy.engine import PolicyDecision, decide
from capabledeputy.policy.labels import CategoryTag, LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import Session
from capabledeputy.substrate.decision_inspector_port import DecisionTighten

POLICIES = Path(__file__).resolve().parents[2] / "configs" / "policies"


def test_bounded_read_has_no_first_use_approval_and_missing_cap_is_denied():
    action = Action(kind=CapabilityKind.IMAP_READ, target="INBOX")
    cap = Capability(kind=CapabilityKind.IMAP_READ, pattern="INBOX")
    assert (
        decide(frozenset({cap}), action, frozenset(), first_use_prompt_enabled=True).decision
        == Decision.ALLOW
    )
    assert decide(frozenset(), action, frozenset()).decision == Decision.DENY


def test_send_capability_does_not_remove_untrusted_floor():
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="alice@example.com")
    labels = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    result = decide(
        frozenset({cap}),
        Action(kind=cap.kind, target="alice@example.com"),
        frozenset(),
        labels=labels,
    )
    assert result.decision == Decision.DENY
    assert result.rule == "untrusted-meets-egress"


@pytest.mark.parametrize("kind", [CapabilityKind.IMAP_READ, CapabilityKind.EXTERNAL_MAIL_DRAFT])
async def test_purpose_relax_never_grants_email_read_or_external_draft(kind):
    pytest.importorskip("starlark")
    (inspector,) = load_decision_inspectors(
        {"decision_inspectors": [{"script": "purpose_scoped_relax.star", "runtime": "starlark"}]},
        base_dir=POLICIES,
    )
    assert (
        await inspector.inspect(
            action=Action(kind=kind, target="INBOX"),
            session=Session.new(purpose_handle="inbox"),
            proposed_outcome=PolicyDecision(
                decision=Decision.REQUIRE_APPROVAL, rule="test", reason="test"
            ),
        )
        is None
    )


async def test_sensitive_starlark_confirms_send_but_does_not_prompt_for_read():
    pytest.importorskip("starlark")
    (inspector,) = load_decision_inspectors(
        {
            "decision_inspectors": [
                {"script": "sensitive_egress_confirm.star", "runtime": "starlark"}
            ]
        },
        base_dir=POLICIES,
    )
    session = replace(
        Session.new(purpose_handle="inbox"),
        label_state=LabelState(a=frozenset({CategoryTag("personal", Tier.REGULATED)})),
    )
    proposed = PolicyDecision(decision=Decision.ALLOW, rule="test", reason="test")
    assert (
        await inspector.inspect(
            action=Action(kind=CapabilityKind.IMAP_READ, target="INBOX"),
            session=session,
            proposed_outcome=proposed,
        )
        is None
    )
    out = await inspector.inspect(
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com"),
        session=session,
        proposed_outcome=proposed,
    )
    assert isinstance(out, DecisionTighten)
    assert out.to == Decision.REQUIRE_APPROVAL
