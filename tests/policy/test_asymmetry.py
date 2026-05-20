"""T037 — Asymmetry invariant (FR-031 / T046).

The decision pipeline accepts deterministic relax inputs
(operator-config, human-ratified-rule, curated-mcp) but refuses any
non-deterministic origin — model suggestion, planner heuristic,
runtime anomaly score, etc. A refusal:
  - Produces a DENY decision with rule = RELAX_REFUSED_RULE.
  - Lists the refused inputs on PolicyDecision.refused_relax_inputs.
  - Causes the audit emission path to fire a stand-alone
    RELAXATION_REFUSED event in addition to policy.decided.
"""

from __future__ import annotations

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import (
    ALLOWED_RELAX_ORIGINS,
    DecisionRuleError,
    RelaxInput,
    inspect_relax_inputs,
    refuse_non_deterministic_relax_input,
)
from capabledeputy.policy.engine import (
    RELAX_REFUSED_RULE,
    decide,
)
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import Decision
from capabledeputy.tools.client import (
    build_policy_decided_payload,
    build_relaxation_refused_payload,
)


def _send_email_cap() -> Capability:
    return Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="alice@example.com",
        origin=CapabilityOrigin.USER_APPROVED,
    )


def _send_action() -> Action:
    return Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com")


# --- pure-function asymmetry checks ----------------------------------


def test_allowed_relax_origins_pinned() -> None:
    """The whitelist itself is part of the policy contract; pinning it
    keeps a future widening visible in code review."""
    assert (
        frozenset(
            {"operator-config", "human-ratified-rule", "curated-mcp"},
        )
        == ALLOWED_RELAX_ORIGINS
    )


def test_refuse_helper_raises_for_non_deterministic_origin() -> None:
    for origin in ["llm-suggested", "planner-heuristic", "anomaly-detector", ""]:
        try:
            refuse_non_deterministic_relax_input(input_origin=origin)
        except DecisionRuleError:
            continue
        raise AssertionError(f"expected refusal for origin={origin!r}")


def test_refuse_helper_accepts_deterministic_origins() -> None:
    for origin in sorted(ALLOWED_RELAX_ORIGINS):
        refuse_non_deterministic_relax_input(input_origin=origin)  # no raise


def test_inspect_partitions_accepted_and_refused() -> None:
    inputs = (
        RelaxInput(description="rule-fired", origin="human-ratified-rule"),
        RelaxInput(description="planner-hint", origin="planner-heuristic"),
        RelaxInput(description="config-derived", origin="operator-config"),
        RelaxInput(description="model-suggestion", origin="llm-suggested"),
    )
    result = inspect_relax_inputs(inputs)
    assert len(result.accepted) == 2
    assert len(result.refused) == 2
    assert result.has_refusal
    refused_origins = {r.origin for r in result.refused}
    assert refused_origins == {"planner-heuristic", "llm-suggested"}


# --- engine.decide() asymmetry behavior ------------------------------


def test_decide_refuses_with_non_deterministic_relax_input() -> None:
    """Even with a fully-allowable legacy ALLOW, a single tainted
    relax input ⇒ entire decision is refused (FR-031)."""
    bad = RelaxInput(description="model said it's fine", origin="llm-suggested")
    result = decide(
        label_set=frozenset({Label.TRUSTED_USER_DIRECT}),
        capabilities=frozenset({_send_email_cap()}),
        action=_send_action(),
        relax_inputs=(bad,),
    )
    assert result.decision == Decision.DENY
    assert result.rule == RELAX_REFUSED_RULE
    assert result.refused_relax_inputs == (bad,)
    assert "non-deterministic" in (result.reason or "")


def test_decide_passes_with_only_deterministic_relax_inputs() -> None:
    """All-allowed relax inputs ⇒ they don't short-circuit; the
    legacy path runs normally."""
    good = (
        RelaxInput(description="from rules.yaml", origin="operator-config"),
        RelaxInput(description="from MCP", origin="curated-mcp"),
    )
    result = decide(
        label_set=frozenset({Label.TRUSTED_USER_DIRECT}),
        capabilities=frozenset({_send_email_cap()}),
        action=_send_action(),
        relax_inputs=good,
    )
    assert result.decision == Decision.ALLOW
    assert result.refused_relax_inputs == ()


def test_decide_refuses_even_one_tainted_among_many() -> None:
    """Any single non-deterministic input poisons the batch."""
    inputs = (
        RelaxInput(description="ok-1", origin="operator-config"),
        RelaxInput(description="ok-2", origin="curated-mcp"),
        RelaxInput(description="tainted", origin="planner-heuristic"),
    )
    result = decide(
        label_set=frozenset({Label.TRUSTED_USER_DIRECT}),
        capabilities=frozenset({_send_email_cap()}),
        action=_send_action(),
        relax_inputs=inputs,
    )
    assert result.decision == Decision.DENY
    assert result.rule == RELAX_REFUSED_RULE
    assert len(result.refused_relax_inputs) == 1
    assert result.refused_relax_inputs[0].origin == "planner-heuristic"


# --- audit payload shape ---------------------------------------------


def test_policy_decided_payload_surfaces_refused_inputs() -> None:
    """The refused inputs appear in the policy.decided payload —
    audit replay needs them to reconstruct the asymmetry refusal."""
    bad = RelaxInput(description="model said it's fine", origin="llm-suggested")
    decision = decide(
        label_set=frozenset({Label.TRUSTED_USER_DIRECT}),
        capabilities=frozenset({_send_email_cap()}),
        action=_send_action(),
        relax_inputs=(bad,),
    )
    payload = build_policy_decided_payload(
        "email.send",
        {"to": "alice@example.com"},
        decision,
    )
    assert payload["decision"] == "deny"
    assert payload["refused_relax_inputs"] == [
        {"description": "model said it's fine", "origin": "llm-suggested"},
    ]


def test_relaxation_refused_payload_shape() -> None:
    bad = RelaxInput(description="model said it's fine", origin="llm-suggested")
    decision = decide(
        label_set=frozenset({Label.TRUSTED_USER_DIRECT}),
        capabilities=frozenset({_send_email_cap()}),
        action=_send_action(),
        relax_inputs=(bad,),
    )
    payload = build_relaxation_refused_payload(
        "email.send",
        {"to": "alice@example.com"},
        decision,
    )
    assert payload["tool"] == "email.send"
    assert payload["args"] == {"to": "alice@example.com"}
    assert payload["refused_relax_inputs"] == [
        {"description": "model said it's fine", "origin": "llm-suggested"},
    ]
    assert payload["decision_rule"] == RELAX_REFUSED_RULE
