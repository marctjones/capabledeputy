"""Composition Sub-phase E — Reversibility gating in decide() (Demo #4).

The demo: agent burns through reversible/system + non-egressing
work autonomously. The same agent surfaces for approval the moment
the work becomes friction'd or non-system-reversal. Irreversible
work denies. Social.* effects always deny no matter what was
declared.

These tests pin the decide() composition. The legacy / v2 layers
still apply most-restrictively; the optimistic-auto carve-out only
relaxes when the v2 leg's default would otherwise have surfaced.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import DecisionRules
from capabledeputy.policy.engine import (
    OPTIMISTIC_AUTO_RULE,
    REVERSIBILITY_IRREVERSIBLE_RULE,
    decide,
)
from capabledeputy.policy.labels import (
    AxisA,
    AxisB,
    AxisBEntry,
    AxisD,
    Label,
    ProvenanceLevel,
)
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient, PolicyContext
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _scratch_cap() -> Capability:
    return Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="/scratch/*",
        origin=CapabilityOrigin.USER_APPROVED,
        allows_destructive=True,
    )


def _empty_axes() -> tuple[AxisA, AxisB, AxisD]:
    return (
        AxisA(),
        AxisB(entries=(AxisBEntry(level=ProvenanceLevel.PRINCIPAL_DIRECT),)),
        AxisD(initiator="principal:alice"),
    )


def _r(degree: ReversibilityDegree, agent: ReversalAgent) -> ReversibilityLabel:
    return ReversibilityLabel(degree=degree, agent=agent)


# --- engine.decide() level ------------------------------------------


def test_reversible_system_non_egressing_optimistic_auto() -> None:
    """The headline case (Demo #4): reversible/system + non-egressing
    ⇒ AUTO without prompt. The v2 default would otherwise have been
    SUGGEST → REQUIRE_APPROVAL; the optimistic carve-out relaxes it
    to ALLOW."""
    axis_a, axis_b, axis_d = _empty_axes()
    result = decide(
        frozenset(),
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.REVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
    )
    assert result.decision == Decision.ALLOW
    assert result.rule == OPTIMISTIC_AUTO_RULE


def test_reversible_system_egressing_does_not_auto() -> None:
    """Same reversibility, but the effect class marks egress (e.g.,
    write_remote). Optimistic carve-out doesn't apply."""
    axis_a, axis_b, axis_d = _empty_axes()
    result = decide(
        frozenset(),
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_remote",  # ← egressing
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.REVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
    )
    # Egress prevents optimistic auto; v2 default SUGGEST surfaces
    # as REQUIRE_APPROVAL.
    assert result.decision == Decision.REQUIRE_APPROVAL


def test_irreversible_effect_denies() -> None:
    """Irreversible effect ⇒ DENY regardless of capability holdings."""
    axis_a, axis_b, axis_d = _empty_axes()
    result = decide(
        frozenset(),
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.IRREVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
    )
    assert result.decision == Decision.DENY
    assert result.rule == REVERSIBILITY_IRREVERSIBLE_RULE


def test_reversible_human_requires_approval() -> None:
    """Reversal-agent=human ⇒ optimistic carve-out doesn't fire;
    the gate produces REQUIRE_APPROVAL. (The composing v2 default
    already ratchets to REQUIRE_APPROVAL too; either rule wins on
    a tie. What matters is the decision.)"""
    axis_a, axis_b, axis_d = _empty_axes()
    result = decide(
        frozenset(),
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.REVERSIBLE,
            ReversalAgent.HUMAN,
        ),
        now=_NOW,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL


def test_social_commitment_forced_irreversible_even_if_declared_reversible() -> None:
    """FR-019 hard rule: social.send_email is always treated
    irreversible. A reversible/system declaration on the tool DOES
    NOT win. Use a matching capability so the legacy path doesn't
    DENY on missing-cap; the gate's DENY is the decision."""
    axis_a, axis_b, axis_d = _empty_axes()
    matching_cap = Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        origin=CapabilityOrigin.USER_APPROVED,
        allows_destructive=True,
    )
    result = decide(
        frozenset(),
        frozenset({matching_cap}),
        Action(kind=CapabilityKind.WRITE_FS, target="x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="social.send_email",
        rules_v2=DecisionRules(rules=()),
        effective_reversibility=_r(
            ReversibilityDegree.REVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
    )
    assert result.decision == Decision.DENY
    assert result.rule == REVERSIBILITY_IRREVERSIBLE_RULE


def test_legacy_only_path_also_applies_gate() -> None:
    """If the caller passes effective_reversibility but no v2 args,
    the reversibility gate still applies (back-compat path stays
    consistent)."""
    result = decide(
        frozenset({Label.TRUSTED_USER_DIRECT}),
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        effect_class="data.write_scratch",
        effective_reversibility=_r(
            ReversibilityDegree.IRREVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        now=_NOW,
    )
    assert result.decision == Decision.DENY
    assert result.rule == REVERSIBILITY_IRREVERSIBLE_RULE


def test_no_reversibility_supplied_falls_to_v2_default() -> None:
    """Back-compat: without effective_reversibility, the gate is
    inert and the v2 default fires normally."""
    axis_a, axis_b, axis_d = _empty_axes()
    result = decide(
        frozenset(),
        frozenset({_scratch_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/scratch/file"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write_scratch",
        rules_v2=DecisionRules(rules=()),
        # no effective_reversibility
        now=_NOW,
    )
    assert result.decision == Decision.REQUIRE_APPROVAL  # v2 default SUGGEST


# --- end-to-end via LabeledToolClient -------------------------------


@pytest.fixture
def writer(tmp_path: Any) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


async def _noop_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(output={"ok": True})


def _reversible_tool() -> ToolDefinition:
    return ToolDefinition(
        name="scratch.write",
        description="t",
        capability_kind=CapabilityKind.WRITE_FS,
        handler=_noop_handler,
        target_arg="path",
        effect_class="data.write_scratch",
        default_reversibility={"degree": "reversible", "agent": "system"},
    )


def _social_tool() -> ToolDefinition:
    return ToolDefinition(
        name="social.send",
        description="t",
        capability_kind=CapabilityKind.SEND_EMAIL,
        handler=_noop_handler,
        target_arg="to",
        effect_class="social.send_email",
        default_reversibility={"degree": "reversible", "agent": "system"},
        social_commitment=True,
    )


async def _make_session_with_cap(graph: SessionGraph, cap: Capability) -> Any:
    s = await graph.new()
    s = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
        capability_set=frozenset({cap}),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )
    graph._sessions[s.id] = s
    return s


async def test_end_to_end_optimistic_auto_no_prompt(writer: AuditWriter) -> None:
    """Demo #4 e2e: reversible/system + non-egressing tool fires
    without a prompt. The agent gets the ALLOW with rule
    OPTIMISTIC_AUTO_RULE."""
    registry = ToolRegistry()
    registry.register(_reversible_tool())
    graph = SessionGraph()
    s = await _make_session_with_cap(graph, _scratch_cap())
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(rules_v2=DecisionRules(rules=())),
    )
    outcome = await client.call_tool(
        s.id,
        "scratch.write",
        {"path": "/scratch/work.txt"},
    )
    assert outcome.decision == Decision.ALLOW
    assert outcome.output == {"ok": True}


async def test_end_to_end_social_send_denies_despite_declared_reversible(
    writer: AuditWriter,
) -> None:
    """Demo #4 e2e (social commitment branch): the tool declared
    reversible/system, but FR-019 forces irreversible because the
    effect_class is in the social-commitment set."""
    registry = ToolRegistry()
    registry.register(_social_tool())
    graph = SessionGraph()
    s = await _make_session_with_cap(
        graph,
        Capability(
            kind=CapabilityKind.SEND_EMAIL,
            pattern="*",
            origin=CapabilityOrigin.USER_APPROVED,
        ),
    )
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(rules_v2=DecisionRules(rules=())),
    )
    outcome = await client.call_tool(
        s.id,
        "social.send",
        {"to": "alice@example.com"},
    )
    assert outcome.decision == Decision.DENY
    assert outcome.rule == REVERSIBILITY_IRREVERSIBLE_RULE
