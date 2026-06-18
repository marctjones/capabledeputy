from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.model import EnforcementMode, Session
from capabledeputy.substrate.declassifier_port import DeclassifyResult
from capabledeputy.substrate.decision_inspector_port import DecisionRelax
from capabledeputy.substrate.inspector_port import InspectorRaiseResult
from capabledeputy.tools.policy_hooks import ToolPolicyHooks


class _RelaxInspector:
    name = "relaxer"

    def inspect(self, *, action, session, proposed_outcome):
        return DecisionRelax(to=Decision.ALLOW, rule="operator-ok", rationale="ok")


class _SchemaDeclassifier:
    name = "schema"

    def declassify(self, *, value, current_label_state, context=None):
        return DeclassifyResult(
            transformed_value={"safe": value["safe"]},
            lower_axis_b_level=ProvenanceLevel.PRINCIPAL_DIRECT.value,
            audit_diff="projected",
            structural_proof_kind="schema-projected",
        )


class _HealthInspector:
    def inspect(self, *, value, current_label_state):
        return InspectorRaiseResult(
            LabelState(
                a=frozenset(
                    {
                        CategoryTag(
                            "health",
                            Tier.REGULATED,
                            assignment_provenance="source-declared",
                        ),
                    },
                ),
            ),
        )


async def test_shadow_rewrite_delegates_policy_shadow_event(tmp_path) -> None:
    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    session = replace(Session.new(), enforcement_mode=EnforcementMode.SHADOW)
    hooks = ToolPolicyHooks(policy_context=PolicyContext(), audit=audit, graph=graph)

    adjusted = await hooks.maybe_shadow_rewrite(
        uuid4(),
        session,
        "email.send",
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        PolicyDecision(decision=Decision.REQUIRE_APPROVAL, rule="v2:rule", reason="gate"),
    )

    assert adjusted.decision == Decision.ALLOW
    assert "shadowed" in (adjusted.reason or "")
    events = await audit.read_all()
    assert events[0].event_type == EventType.POLICY_SHADOWED


async def test_decision_inspector_relaxes_require_approval(tmp_path) -> None:
    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(decision_inspectors=(_RelaxInspector(),)),
        audit=audit,
        graph=graph,
    )

    adjusted = await hooks.apply_decision_inspectors(
        uuid4(),
        Session.new(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        "email.send",
        PolicyDecision(decision=Decision.REQUIRE_APPROVAL, rule="base"),
    )

    assert adjusted.decision == Decision.ALLOW
    assert adjusted.rule == "relaxer:operator-ok"
    events = await audit.read_all()
    assert events[0].event_type == EventType.DECISION_INSPECTOR_APPLIED


async def test_declassifier_transforms_output_and_removes_provenance_tag(tmp_path) -> None:
    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(declassifiers=(_SchemaDeclassifier(),)),
        audit=audit,
        graph=graph,
    )
    untrusted = LabelState(
        b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
    )

    value, removed = await hooks.apply_declassifiers(
        uuid4(),
        Session.new(),
        "memory.read",
        {"safe": "keep", "secret": "drop"},
        untrusted,
        LabelState(),
    )

    assert value == {"safe": "keep"}
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in removed.b
    events = await audit.read_all()
    assert events[0].event_type == EventType.DECLASSIFIER_APPLIED


async def test_raise_only_inspector_persists_session_taint(tmp_path) -> None:
    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    session = await graph.new()
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(inspectors=(_HealthInspector(),)),
        audit=audit,
        graph=graph,
    )

    await hooks.apply_inspectors(session, "medical note")

    updated = graph.get(session.id)
    assert CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared") in (
        updated.label_state.a
    )
    events = await audit.read_all()
    assert any(event.event_type == EventType.INSPECTOR_APPLIED for event in events)
