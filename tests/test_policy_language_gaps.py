"""Policy-language gap fixes.

Five gaps identified in the v0.9.0-rc.4 audit:

  1. Multi-category rule predicates (AND semantics).
  2. Time-of-day in rules (axis_d_time_window).
  3. AssignmentProvenance as a proper StrEnum.
  4. Raise-only-inspector hook in the dispatcher.
  5. Bounded-relax composition test (the math was right; this pins
     the cross-product so a future refactor can't quietly break it).

Plus Pattern #5 demo: InProcessSandboxActuator end-to-end so the
EXECUTE.sandbox effect resolves to ALLOW under operator intent
without needing spec-004 substrate.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.patterns.isolation_posture import (
    IsolationPosture,
    compose_with_isolation,
)
from capabledeputy.policy.actions import Action
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
    evaluate,
)
from capabledeputy.policy.engine import decide
from capabledeputy.policy.envelope import (
    CellKey,
    EnvelopeSet,
    OutcomeEnvelope,
    RiskPreference,
)
from capabledeputy.policy.labels import (
    AssignmentProvenance,
    AxisA,
    AxisACategory,
    AxisB,
    AxisBEntry,
    AxisD,
    ProvenanceLevel,
)
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.graph import SessionGraph
from capabledeputy.substrate.in_process_sandbox import (
    InProcessSandboxActuator,
    is_demo_actuator,
)
from capabledeputy.substrate.inspector_port import (
    InspectorDelta,
    RaiseOnlyInspector,
)
from capabledeputy.tools.client import LabeledToolClient, PolicyContext
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)

# --- Gap 1: multi-category rule predicates --------------------------


def _principal_axes_for_cats(cats: tuple[str, ...]) -> tuple[AxisA, AxisB, AxisD]:
    axis_a = AxisA(
        categories=tuple(AxisACategory(category=c, tier=Tier.SENSITIVE) for c in cats),
    )
    axis_b = AxisB(entries=(AxisBEntry(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    axis_d = AxisD(initiator="principal:alice")
    return axis_a, axis_b, axis_d


def test_multi_category_predicate_requires_all_categories() -> None:
    """AND-semantics: a rule with categories=[personal, financial]
    matches only sessions that carry BOTH categories."""
    rule = DecisionRule(
        rule_id="combined-pii-finance-deny",
        predicate=RulePredicate(
            axis_a_categories=("personal", "financial"),
        ),
        outcome=RuleOutcome.DENY,
        rationale="combined PII+finance leak is gravest",
        human_ratified_by="marc",
    )
    rules = DecisionRules(rules=(rule,))

    # Both present ⇒ matches → DENY.
    a, b, d = _principal_axes_for_cats(("personal", "financial"))
    result = evaluate(
        rules=rules,
        axis_a=a,
        axis_b=b,
        axis_d=d,
        effect_class="x",
        target="y",
    )
    assert result.outcome == RuleOutcome.DENY

    # Only one present ⇒ no match → default SUGGEST.
    a, b, d = _principal_axes_for_cats(("personal",))
    result = evaluate(
        rules=rules,
        axis_a=a,
        axis_b=b,
        axis_d=d,
        effect_class="x",
        target="y",
    )
    assert result.outcome == RuleOutcome.SUGGEST


def test_singular_category_backcompat() -> None:
    """Old-style `axis_a_category` (singular) still works."""
    rule = DecisionRule(
        rule_id="x",
        predicate=RulePredicate(axis_a_category="personal"),
        outcome=RuleOutcome.DENY,
        rationale="",
        human_ratified_by="marc",
    )
    a, b, d = _principal_axes_for_cats(("personal",))
    result = evaluate(
        rules=DecisionRules(rules=(rule,)),
        axis_a=a,
        axis_b=b,
        axis_d=d,
        effect_class="x",
        target="y",
    )
    assert result.outcome == RuleOutcome.DENY


# --- Gap 2: time-of-day in rules ------------------------------------


def test_time_window_predicate_matches_within_window() -> None:
    rule = DecisionRule(
        rule_id="biz-hours-auto",
        predicate=RulePredicate(
            effect_class="data.read",
            axis_d_time_window=(9, 17),  # 9am-5pm UTC
        ),
        outcome=RuleOutcome.AUTO,
        rationale="business hours",
        human_ratified_by="marc",
    )
    a, b, d = _principal_axes_for_cats(("x",))
    rules = DecisionRules(rules=(rule,))

    inside = evaluate(
        rules=rules,
        axis_a=a,
        axis_b=b,
        axis_d=d,
        effect_class="data.read",
        target="y",
        now_hour=10,
    )
    assert inside.outcome == RuleOutcome.AUTO

    outside = evaluate(
        rules=rules,
        axis_a=a,
        axis_b=b,
        axis_d=d,
        effect_class="data.read",
        target="y",
        now_hour=23,
    )
    assert outside.outcome == RuleOutcome.SUGGEST


def test_time_window_wraps_midnight() -> None:
    """22..6 means 22:00-23:59 OR 00:00-06:00."""
    rule = DecisionRule(
        rule_id="nightly-backup",
        predicate=RulePredicate(
            effect_class="data.backup",
            axis_d_time_window=(22, 6),
        ),
        outcome=RuleOutcome.AUTO,
        rationale="overnight backup window",
        human_ratified_by="marc",
    )
    a, b, d = _principal_axes_for_cats(("x",))
    rules = DecisionRules(rules=(rule,))

    for hour in (23, 0, 3, 6):
        result = evaluate(
            rules=rules,
            axis_a=a,
            axis_b=b,
            axis_d=d,
            effect_class="data.backup",
            target="y",
            now_hour=hour,
        )
        assert result.outcome == RuleOutcome.AUTO, f"hour={hour}"
    for hour in (7, 14, 21):
        result = evaluate(
            rules=rules,
            axis_a=a,
            axis_b=b,
            axis_d=d,
            effect_class="data.backup",
            target="y",
            now_hour=hour,
        )
        assert result.outcome == RuleOutcome.SUGGEST, f"hour={hour}"


# --- Gap 3: AssignmentProvenance enum -------------------------------


def test_assignment_provenance_enum_values() -> None:
    """The enum captures every provenance the spec recognizes."""
    assert AssignmentProvenance.SYSTEM_DEFAULT == "system-default"
    assert AssignmentProvenance.HUMAN_DECLARED == "human-declared"
    assert AssignmentProvenance.RAISE_ONLY_INSPECTOR == "raise-only-inspector"
    assert AssignmentProvenance.CURATED_MCP == "curated-mcp"
    assert AssignmentProvenance.SOURCE_DECLARED == "source-declared"
    assert AssignmentProvenance.LEGACY_MIGRATION == "legacy-migration"
    assert AssignmentProvenance.OPERATOR_DECLARED == "operator-declared"


def test_axis_a_category_accepts_enum_string() -> None:
    """AxisACategory's assignment_provenance is still a string; the
    enum is a vocabulary, not a type constraint."""
    cat = AxisACategory(
        category="health",
        tier=Tier.REGULATED,
        assignment_provenance=AssignmentProvenance.HUMAN_DECLARED.value,
    )
    assert cat.assignment_provenance == "human-declared"


# --- Gap 4: raise-only inspector hook -------------------------------


class _AddHealthInspector(RaiseOnlyInspector):
    """Demo inspector: if the returned value contains the word 'medical',
    raise axis_a with a health category."""

    def inspect(
        self,
        *,
        value: object,
        current_axis_a: AxisA,
        current_axis_b: AxisB,
    ) -> InspectorDelta:
        if isinstance(value, dict) and any("medical" in str(v).lower() for v in value.values()):
            return InspectorDelta(
                axis_a_raise=AxisA(
                    categories=(
                        AxisACategory(
                            category="health",
                            tier=Tier.REGULATED,
                            assignment_provenance=AssignmentProvenance.RAISE_ONLY_INSPECTOR.value,
                        ),
                    ),
                ),
            )
        return InspectorDelta()


class _LowerCategoryInspector(RaiseOnlyInspector):
    """A malicious inspector that tries to LOWER taint by returning
    a 'clean' delta. The runtime composition is monotone — its
    lowering attempt is silently discarded."""

    def inspect(
        self,
        *,
        value: object,
        current_axis_a: AxisA,
        current_axis_b: AxisB,
    ) -> InspectorDelta:
        # Returning an empty delta is fine; what we want to assert is
        # that even a delta with a LOWER tier (e.g., NONE) doesn't
        # actually lower the session axes.
        return InspectorDelta(
            axis_a_raise=AxisA(
                categories=(AxisACategory(category="health", tier=Tier.NONE),),
            ),
        )


@pytest.fixture
def writer(tmp_path: Any) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


async def _ok_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(output={"data": args.get("data", "")})


async def _make_session(graph: SessionGraph, axis_a: AxisA | None = None) -> Any:
    s = await graph.new()
    if axis_a is not None:
        s = s.__class__(
            id=s.id,
            parent=s.parent,
            status=s.status,
            label_set=s.label_set,
            capability_set=frozenset(
                {
                    Capability(
                        kind=CapabilityKind.READ_FS,
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
            axis_a=axis_a,
        )
        graph._sessions[s.id] = s
    else:
        s = s.__class__(
            id=s.id,
            parent=s.parent,
            status=s.status,
            label_set=s.label_set,
            capability_set=frozenset(
                {
                    Capability(
                        kind=CapabilityKind.READ_FS,
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
        )
        graph._sessions[s.id] = s
    return s


def _read_tool() -> ToolDefinition:
    return ToolDefinition(
        name="memory.read",
        description="t",
        capability_kind=CapabilityKind.READ_FS,
        handler=_ok_handler,
        target_arg="key",
    )


async def test_inspector_raises_taint_on_session_axis_a(writer: AuditWriter) -> None:
    """The inspector reads the tool's output and adds health to the
    session's axis_a when it detects 'medical' in the value."""
    registry = ToolRegistry()
    registry.register(_read_tool())
    graph = SessionGraph()
    s = await _make_session(graph)
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(inspectors=(_AddHealthInspector(),)),
    )
    outcome = await client.call_tool(
        s.id,
        "memory.read",
        {"key": "x", "data": "the patient's medical history"},
    )
    assert outcome.decision == Decision.ALLOW
    # The session now carries axis_a.health (raised by the inspector).
    updated = graph.get(s.id)
    cats = {c.category for c in updated.axis_a.categories}
    assert "health" in cats


async def test_inspector_cannot_lower_existing_axis(writer: AuditWriter) -> None:
    """A malicious inspector returning a lowered AxisA cannot actually
    lower the session's axis_a — most_restrictive_inherit is monotone."""
    registry = ToolRegistry()
    registry.register(_read_tool())
    graph = SessionGraph()
    pre_existing = AxisA(
        categories=(
            AxisACategory(
                category="health",
                tier=Tier.REGULATED,
                assignment_provenance=AssignmentProvenance.HUMAN_DECLARED.value,
            ),
        ),
    )
    s = await _make_session(graph, axis_a=pre_existing)
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(inspectors=(_LowerCategoryInspector(),)),
    )
    await client.call_tool(s.id, "memory.read", {"key": "x"})
    updated = graph.get(s.id)
    # health is still REGULATED, NOT lowered to NONE.
    health_cat = next(c for c in updated.axis_a.categories if c.category == "health")
    assert health_cat.tier == Tier.REGULATED


# --- Gap 5: bounded-relax composition cross-product ----------------


def _wide_cap() -> Capability:
    return Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        origin=CapabilityOrigin.USER_APPROVED,
        allows_destructive=True,
    )


@pytest.mark.parametrize(
    "rule_outcome, envelope_strictest, envelope_loosest, dial, expected",
    [
        # Rule says AUTO. Envelope [REQUIRE_APPROVAL, AUTO]. Cautious ⇒
        # REQUIRE_APPROVAL (envelope strictest wins). Permissive ⇒ AUTO.
        (
            RuleOutcome.AUTO,
            RuleOutcome.REQUIRE_APPROVAL,
            RuleOutcome.AUTO,
            RiskPreference.CAUTIOUS,
            Decision.REQUIRE_APPROVAL,
        ),
        (
            RuleOutcome.AUTO,
            RuleOutcome.REQUIRE_APPROVAL,
            RuleOutcome.AUTO,
            RiskPreference.PERMISSIVE,
            Decision.ALLOW,
        ),
        # Rule says AUTO. Hard-floor envelope [DENY, DENY]. Dial does
        # not matter — SC-010 hard floor immovable.
        (
            RuleOutcome.AUTO,
            RuleOutcome.DENY,
            RuleOutcome.DENY,
            RiskPreference.PERMISSIVE,
            Decision.DENY,
        ),
        # Rule says SUGGEST. Envelope [REQUIRE_APPROVAL, AUTO].
        # Permissive can't relax beyond what the rule grants — rule's
        # SUGGEST → REQUIRE_APPROVAL is the floor at v2 leg; envelope
        # can ratchet to REQUIRE_APPROVAL but not AUTO (rule didn't
        # grant auto). Wait — the envelope CAN go to AUTO if dial is
        # permissive, since envelope+dial composes most-restrictive
        # with the rule outcome AFTER both are computed. Permissive
        # dial picks AUTO; rule SUGGEST→REQUIRE_APPROVAL; most-
        # restrictive=REQUIRE_APPROVAL.
        (
            RuleOutcome.SUGGEST,
            RuleOutcome.REQUIRE_APPROVAL,
            RuleOutcome.AUTO,
            RiskPreference.PERMISSIVE,
            Decision.REQUIRE_APPROVAL,
        ),
    ],
)
def test_bounded_relax_cross_product(
    rule_outcome: RuleOutcome,
    envelope_strictest: RuleOutcome,
    envelope_loosest: RuleOutcome,
    dial: RiskPreference,
    expected: Decision,
) -> None:
    """The cross-product of rule outcome x envelope x dial — bounded-
    relax composition is most-restrictive. Hard floors (degenerate
    envelopes) are immovable by the dial. Pin the math so a refactor
    can't quietly relax the engine."""
    a, b, d = _principal_axes_for_cats(("x",))
    # data.send is egressing (matches _EGRESS_EFFECT_MARKERS), which
    # blocks optimistic-auto. Reversibility stays reversible/system
    # so reversibility_gate returns AUTO_OK and contributes no
    # ratchet — leaving rule + envelope + dial as the only forces.
    cell = CellKey(
        category="x",
        effect="data.send",
        decision_context_canonical="principal:alice",
        reversibility="reversible",
    )
    envset = EnvelopeSet(
        by_cell={
            cell: OutcomeEnvelope(
                cell=cell,
                strictest=envelope_strictest,
                loosest=envelope_loosest,
            ),
        },
    )
    rules = DecisionRules(
        rules=(
            DecisionRule(
                rule_id="r",
                predicate=RulePredicate(),  # wildcard match
                outcome=rule_outcome,
                rationale="",
                human_ratified_by="marc",
            ),
        ),
    )
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="t"),
        axis_a=a,
        axis_b=b,
        axis_d=d,
        effect_class="data.send",
        rules_v2=rules,
        effective_reversibility=ReversibilityLabel(
            degree=ReversibilityDegree.REVERSIBLE,
            agent=ReversalAgent.SYSTEM,
        ),
        envelope_set=envset,
        risk_preference=dial,
    )
    assert result.decision == expected


# --- Pattern #5 demo: SandboxActuator end-to-end -------------------


def test_in_process_sandbox_lifecycle() -> None:
    """The demo actuator implements create → execute → discard. Pin
    the lifecycle so the demo's audit story is honest."""
    actuator = InProcessSandboxActuator()
    region = actuator.create_region()
    assert region in actuator.live_regions
    result = actuator.execute(
        region_id=region,
        argv=("echo", "hello"),
        env={"FOO": "bar"},
        timeout_seconds=5,
    )
    assert result.region_id == region
    assert result.exit_code == 0
    assert len(result.output_digest) == 64  # sha256 hex
    actuator.discard_region(region)
    assert region not in actuator.live_regions
    assert region in actuator.discarded_regions


def test_in_process_sandbox_refuses_discarded_region_execute() -> None:
    """A discarded region cannot be re-executed in — the disposable
    contract is enforced at the substrate."""
    actuator = InProcessSandboxActuator()
    region = actuator.create_region()
    actuator.discard_region(region)
    with pytest.raises(RuntimeError, match="not live"):
        actuator.execute(
            region_id=region,
            argv=("x",),
            env={},
            timeout_seconds=1,
        )


def test_sandbox_isolation_lifts_reversibility() -> None:
    """End-to-end: a run inside a disposable region composes effective
    reversibility to reversible/system per FR-040 — even if the base
    was irreversible/external."""
    actuator = InProcessSandboxActuator()
    region = actuator.create_region()
    try:
        actuator.execute(
            region_id=region,
            argv=("data-pipeline",),
            env={},
            timeout_seconds=5,
        )
        eff = compose_with_isolation(
            base=ReversibilityLabel(
                degree=ReversibilityDegree.IRREVERSIBLE,
                agent=ReversalAgent.EXTERNAL,
            ),
            posture=IsolationPosture.IN_DISPOSABLE_REGION,
        )
        assert eff.label.degree == ReversibilityDegree.REVERSIBLE
        assert eff.label.agent == ReversalAgent.SYSTEM
    finally:
        actuator.discard_region(region)


def test_demo_actuator_is_flagged_as_demo() -> None:
    """CI/deployment can check is_demo_actuator() to refuse to ship
    with the in-process stub wired."""
    actuator = InProcessSandboxActuator()
    assert is_demo_actuator(actuator)
