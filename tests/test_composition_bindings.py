"""Composition Sub-phase D — Bindings consulted in decide() (Demo #6, SC-018/SC-022).

The marquee scenario: HR-folder data flowing to TeamSharePoint is
denied deterministically via a named binding — never "no rule
matched." The bind path:

  1. Model proposes a write to some URL.
  2. engine.decide() canonicalizes the URL through bindings.resolve();
     the canonical id replaces action.target for rule predicates.
  3. Operator-authored rules.yaml denies on (axis_a=personal +
     target=canonical-sharepoint-url).
  4. Or: unbound URL ⇒ refuse with rule=binding-unbound.

These tests pin (1)-(4) without depending on the operator-curated
configs (fixtures inject the bindings + rules directly).
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.actions import Action
from capabledeputy.policy.bindings import (
    BindingSet,
    SourceLocationLabelBinding,
)
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import (
    DecisionRule,
    DecisionRules,
    RuleOutcome,
    RulePredicate,
)
from capabledeputy.policy.engine import BINDING_UNBOUND_RULE, decide
from capabledeputy.policy.labels import (
    AxisA,
    AxisACategory,
    AxisB,
    AxisBEntry,
    AxisD,
    ProvenanceLevel,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient, PolicyContext
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


def _team_sharepoint_binding() -> SourceLocationLabelBinding:
    return SourceLocationLabelBinding(
        name="TeamSharePoint",
        scope_pattern_canonical="https://teams.sharepoint.com/*",
        category="proprietary_work",
        default_tier=Tier.SENSITIVE,
    )


def _personal_axis_a() -> AxisA:
    return AxisA(
        categories=(
            AxisACategory(
                category="personal",
                tier=Tier.REGULATED,
                assignment_provenance="human-declared",
            ),
        ),
    )


def _principal_axes() -> tuple[AxisB, AxisD]:
    return (
        AxisB(entries=(AxisBEntry(level=ProvenanceLevel.PRINCIPAL_DIRECT),)),
        AxisD(initiator="principal:alice", authentication="device-bound"),
    )


def _deny_personal_to_sharepoint_rule() -> DecisionRule:
    """Operator-curated rule: personal data + canonical SharePoint URL
    ⇒ DENY. The rule predicate uses the canonical target string."""
    return DecisionRule(
        rule_id="block-personal-to-team-share",
        predicate=RulePredicate(
            axis_a_category="personal",
            target="https://teams.sharepoint.com/sites/x",
        ),
        outcome=RuleOutcome.DENY,
        rationale="personal data may not land on the team share",
        human_ratified_by="marc@example.com",
    )


def _send_cap() -> Capability:
    return Capability(
        kind=CapabilityKind.WEB_FETCH,
        pattern="*",
        origin=CapabilityOrigin.USER_APPROVED,
    )


# --- engine.decide() level ------------------------------------------


def test_unbound_target_fails_closed() -> None:
    """When bindings is wired and the target doesn't match any
    binding, decide() denies with BINDING_UNBOUND_RULE. SC-022."""
    bindings = BindingSet(bindings=(_team_sharepoint_binding(),))
    axis_b, axis_d = _principal_axes()
    result = decide(
        frozenset(),
        frozenset({_send_cap()}),
        Action(kind=CapabilityKind.WEB_FETCH, target="https://random.example.com/x"),
        axis_a=_personal_axis_a(),
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_remote",
        rules_v2=DecisionRules(rules=()),
        bindings=bindings,
    )
    assert result.decision == Decision.DENY
    assert result.rule == BINDING_UNBOUND_RULE


def test_case_varying_url_canonicalizes_then_matches_rule() -> None:
    """SC-018 — the model can vary the case of the destination URL,
    but the rule fires deterministically because the canonical id
    is what the rule predicate sees."""
    bindings = BindingSet(bindings=(_team_sharepoint_binding(),))
    rules = DecisionRules(rules=(_deny_personal_to_sharepoint_rule(),))
    axis_b, axis_d = _principal_axes()
    # Case-varied target
    result = decide(
        frozenset(),
        frozenset({_send_cap()}),
        Action(
            kind=CapabilityKind.WEB_FETCH,
            target="HTTPS://Teams.SharePoint.COM/sites/x",
        ),
        axis_a=_personal_axis_a(),
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_remote",
        rules_v2=rules,
        bindings=bindings,
    )
    assert result.decision == Decision.DENY
    assert result.v2_outcome == RuleOutcome.DENY


def test_bound_target_passes_through_when_no_rule_matches() -> None:
    """When the destination is bound but no rule predicates on it,
    the v2 leg falls to the never-auto default ⇒ REQUIRE_APPROVAL."""
    bindings = BindingSet(bindings=(_team_sharepoint_binding(),))
    axis_b, axis_d = _principal_axes()
    result = decide(
        frozenset(),
        frozenset({_send_cap()}),
        Action(
            kind=CapabilityKind.WEB_FETCH,
            target="https://teams.sharepoint.com/sites/other",
        ),
        axis_a=AxisA(),  # no labeled categories
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_remote",
        rules_v2=DecisionRules(rules=()),
        bindings=bindings,
    )
    # No rule matched ⇒ v2 default SUGGEST → REQUIRE_APPROVAL
    assert result.decision == Decision.REQUIRE_APPROVAL


def test_no_bindings_set_falls_back_to_raw_target() -> None:
    """Back-compat: if PolicyContext doesn't provide bindings, the
    raw target is passed through to rule predicates unchanged."""
    rules = DecisionRules(rules=(_deny_personal_to_sharepoint_rule(),))
    axis_b, axis_d = _principal_axes()
    # Without bindings, only an exact raw-string match against the
    # rule predicate would fire — case-varied input won't.
    result = decide(
        frozenset(),
        frozenset({_send_cap()}),
        Action(
            kind=CapabilityKind.WEB_FETCH,
            target="HTTPS://Teams.SharePoint.COM/sites/x",  # case-varied
        ),
        axis_a=_personal_axis_a(),
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_remote",
        rules_v2=rules,
        # bindings intentionally omitted
    )
    # No bindings ⇒ no canonicalization ⇒ rule predicate doesn't match
    # the case-varied string ⇒ v2 default SUGGEST ⇒ REQUIRE_APPROVAL.
    assert result.decision == Decision.REQUIRE_APPROVAL


# --- end-to-end via LabeledToolClient -------------------------------


@pytest.fixture
def writer(tmp_path: Any) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


async def _noop_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(output={"ok": True})


def _api_post_tool() -> ToolDefinition:
    return ToolDefinition(
        name="api.post",
        description="t",
        capability_kind=CapabilityKind.WEB_FETCH,
        handler=_noop_handler,
        target_arg="url",
        effect_class="data.write_remote",
    )


async def _make_session_with_personal_axis(graph: SessionGraph) -> Any:
    s = await graph.new()
    s = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
        capability_set=frozenset(
            {
                Capability(
                    kind=CapabilityKind.WEB_FETCH,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
        axis_a=_personal_axis_a(),
        axis_b=AxisB(entries=(AxisBEntry(level=ProvenanceLevel.PRINCIPAL_DIRECT),)),
        axis_d=AxisD(initiator="principal:alice", authentication="device-bound"),
    )
    graph._sessions[s.id] = s
    return s


async def test_end_to_end_hr_to_sharepoint_denial(writer: AuditWriter) -> None:
    """The marquee Demo #6 scenario. Session carries personal/regulated
    data; model proposes posting to teams.sharepoint.com; the
    bindings + rules combo denies."""
    registry = ToolRegistry()
    registry.register(_api_post_tool())
    graph = SessionGraph()
    s = await _make_session_with_personal_axis(graph)
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(
            rules_v2=DecisionRules(rules=(_deny_personal_to_sharepoint_rule(),)),
            bindings=BindingSet(bindings=(_team_sharepoint_binding(),)),
        ),
    )
    outcome = await client.call_tool(
        s.id,
        "api.post",
        {"url": "https://teams.sharepoint.com/sites/x"},
    )
    assert outcome.decision == Decision.DENY


async def test_end_to_end_unbound_destination_refused(writer: AuditWriter) -> None:
    """An unbound destination URL ⇒ refused at the chokepoint with
    BINDING_UNBOUND_RULE. SC-022 — never 'no rule matched.'"""
    registry = ToolRegistry()
    registry.register(_api_post_tool())
    graph = SessionGraph()
    s = await _make_session_with_personal_axis(graph)
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(
            rules_v2=DecisionRules(rules=()),
            bindings=BindingSet(bindings=(_team_sharepoint_binding(),)),
        ),
    )
    outcome = await client.call_tool(
        s.id,
        "api.post",
        {"url": "https://random.example.com/x"},
    )
    assert outcome.decision == Decision.DENY
    assert outcome.rule == BINDING_UNBOUND_RULE
