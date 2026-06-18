"""Tests for the decision-inspector loader (#46) — the wire that turns
the dormant decision-refinement layer ON.

Uses the dependency-free `python-reference` script host so the
loader/adapter logic is covered without the optional Starlark extra;
a separate test exercises the real Starlark host when it's installed.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.decision_inspector_loader import (
    DecisionInspectorConfigError,
    ScriptDecisionInspector,
    load_decision_inspectors,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.substrate.decision_inspector_port import (
    DecisionRelax,
    DecisionTighten,
)
from capabledeputy.substrate.decision_inspectors_builtin import (
    AfterHoursPurchaseTightener,
    SelfEgressRelaxer,
)
from capabledeputy.tools.policy_hooks import ToolPolicyHooks


def test_empty_config_yields_no_inspectors() -> None:
    assert load_decision_inspectors(None) == ()
    assert load_decision_inspectors({}) == ()
    assert load_decision_inspectors({"decision_inspectors": []}) == ()


def test_loads_builtin_self_egress_relaxer() -> None:
    cfg = {
        "decision_inspectors": [
            {
                "builtin": "self_egress_relaxer",
                "self_addresses": ["me@example.com"],
                "action_kinds": ["SEND_EMAIL"],
            },
        ],
    }
    (insp,) = load_decision_inspectors(cfg)
    assert isinstance(insp, SelfEgressRelaxer)
    assert insp.self_addresses == frozenset({"me@example.com"})


def test_loads_builtin_after_hours_tightener() -> None:
    cfg = {
        "decision_inspectors": [
            {"builtin": "after_hours_purchase_tightener", "start_hour_utc": 23, "end_hour_utc": 5},
        ],
    }
    (insp,) = load_decision_inspectors(cfg)
    assert isinstance(insp, AfterHoursPurchaseTightener)
    assert insp.start_hour_utc == 23
    assert insp.end_hour_utc == 5


def test_unknown_builtin_fails_closed() -> None:
    with pytest.raises(DecisionInspectorConfigError):
        load_decision_inspectors({"decision_inspectors": [{"builtin": "nope"}]})


def test_malformed_entry_fails_closed() -> None:
    with pytest.raises(DecisionInspectorConfigError):
        load_decision_inspectors({"decision_inspectors": ["not-a-mapping"]})
    with pytest.raises(DecisionInspectorConfigError):
        load_decision_inspectors({"decision_inspectors": [{"no": "type"}]})
    with pytest.raises(DecisionInspectorConfigError):
        load_decision_inspectors({"decision_inspectors": {"not": "a list"}})


def test_bad_script_fails_closed_at_load() -> None:
    cfg = {
        "decision_inspectors": [
            # No `def inspect(` ⇒ compile error ⇒ fail-closed at load.
            {"source": "x = 1", "runtime": "python-reference", "name": "bad"},
        ],
    }
    with pytest.raises(DecisionInspectorConfigError):
        load_decision_inspectors(cfg)


_RELAX_SCRIPT = """
def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] == "require_approval":
        return relax(to="allow", rule="self-test-relax", rationale="ok")
    return abstain()
"""

_TIGHTEN_SCRIPT = """
def inspect(action, session, proposed_outcome):
    if action["kind"] == "QUEUE_PURCHASE" and proposed_outcome["decision"] == "allow":
        return tighten(to="require_approval", rule="extra-scrutiny", rationale="caution")
    return abstain()
"""


def _proposed(decision: Decision):
    from dataclasses import dataclass

    @dataclass
    class _P:
        decision: Decision
        rule: str = "base"
        reason: str = ""

    return _P(decision=decision)


async def test_script_inspector_relaxes() -> None:
    cfg = {
        "decision_inspectors": [
            {"source": _RELAX_SCRIPT, "runtime": "python-reference", "name": "relax"},
        ],
    }
    (insp,) = load_decision_inspectors(cfg)
    assert isinstance(insp, ScriptDecisionInspector)

    action = Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com")
    out = await insp.inspect(
        action=action,
        session=object(),  # session attrs are getattr-with-default tolerant
        proposed_outcome=_proposed(Decision.REQUIRE_APPROVAL),
    )
    assert isinstance(out, DecisionRelax)
    assert out.to == Decision.ALLOW
    assert out.rule == "self-test-relax"


async def test_script_inspector_abstains_when_not_matched() -> None:
    cfg = {
        "decision_inspectors": [
            {"source": _RELAX_SCRIPT, "runtime": "python-reference", "name": "relax"},
        ],
    }
    (insp,) = load_decision_inspectors(cfg)
    out = await insp.inspect(
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com"),
        session=object(),
        proposed_outcome=_proposed(Decision.ALLOW),
    )
    assert out is None


async def test_script_inspector_tightens() -> None:
    cfg = {
        "decision_inspectors": [
            {"source": _TIGHTEN_SCRIPT, "runtime": "python-reference", "name": "tighten"},
        ],
    }
    (insp,) = load_decision_inspectors(cfg)
    out = await insp.inspect(
        action=Action(kind=CapabilityKind.QUEUE_PURCHASE, target="store", amount=10),
        session=object(),
        proposed_outcome=_proposed(Decision.ALLOW),
    )
    assert isinstance(out, DecisionTighten)
    assert out.to == Decision.REQUIRE_APPROVAL


_FREQ_SCRIPT = """
def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "allow":
        return abstain()
    counts = session["history"]["counts_by_kind"]
    if action["kind"] == "SEND_EMAIL" and counts.get("SEND_EMAIL", 0) >= 3:
        return tighten(to="require_approval", rule="freq-cap", rationale="too many")
    return abstain()
"""


async def test_history_summary_enables_frequency_cap(tmp_path) -> None:
    """#48 — the read-only history summary in `session` lets a script
    express a cumulative frequency cap."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from capabledeputy.policy.capabilities import Capability

    (insp,) = load_decision_inspectors(
        {"decision_inspectors": [{"source": _FREQ_SCRIPT, "runtime": "python-reference"}]},
    )

    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    now = datetime.now(UTC)

    def _session(n_uses: int) -> SimpleNamespace:
        return SimpleNamespace(
            capability_set=frozenset({cap}),
            cap_uses={str(cap.audit_id): tuple(now for _ in range(n_uses))},
            used_kinds=frozenset({CapabilityKind.SEND_EMAIL}),
        )

    # 3 prior sends ⇒ at the cap ⇒ tighten.
    out = await insp.inspect(
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com"),
        session=_session(3),
        proposed_outcome=_proposed(Decision.ALLOW),
    )
    assert isinstance(out, DecisionTighten)
    assert out.to == Decision.REQUIRE_APPROVAL

    # Under the threshold ⇒ abstain.
    out2 = await insp.inspect(
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com"),
        session=_session(1),
        proposed_outcome=_proposed(Decision.ALLOW),
    )
    assert out2 is None


_REL_SCRIPT = """
def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "require_approval":
        return abstain()
    if "family" in action["relationship_groups"] and action["kind"] == "SEND_EMAIL":
        return relax(to="allow", rule="family-relax", rationale="vetted")
    return abstain()
"""


async def test_relationship_groups_resolved_into_inspector(tmp_path) -> None:
    """#47 — the chokepoint resolves the target's relationship groups and
    surfaces them to scripts as action['relationship_groups'], enabling a
    relationship-aware relax."""
    from capabledeputy.audit.writer import AuditWriter
    from capabledeputy.policy.engine import PolicyDecision
    from capabledeputy.policy.relationships import RelationshipGroup, RelationshipGroups
    from capabledeputy.session.graph import SessionGraph

    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    (insp,) = load_decision_inspectors(
        {"decision_inspectors": [{"source": _REL_SCRIPT, "runtime": "python-reference"}]},
    )
    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@home.example"}),
            ),
        },
    )
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(decision_inspectors=(insp,), relationship_groups=rg),
        audit=audit,
        graph=graph,
    )
    proposed = PolicyDecision(decision=Decision.REQUIRE_APPROVAL, rule="approval")

    # Recipient in the family group ⇒ relax to ALLOW.
    out = await hooks.apply_decision_inspectors(
        None,
        object(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="spouse@home.example"),
        "email.send",
        proposed,
    )
    assert out.decision == Decision.ALLOW

    # A stranger ⇒ no relax, stays REQUIRE_APPROVAL.
    out2 = await hooks.apply_decision_inspectors(
        None,
        object(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="stranger@elsewhere.example"),
        "email.send",
        proposed,
    )
    assert out2.decision == Decision.REQUIRE_APPROVAL


def test_script_path_relative_to_base_dir(tmp_path) -> None:
    (tmp_path / "p.py").write_text(_RELAX_SCRIPT)
    cfg = {
        "decision_inspectors": [
            {"script": "p.py", "runtime": "python-reference"},
        ],
    }
    (insp,) = load_decision_inspectors(cfg, base_dir=tmp_path)
    assert isinstance(insp, ScriptDecisionInspector)
    assert insp.name == "p"


def test_missing_script_file_fails_closed(tmp_path) -> None:
    cfg = {"decision_inspectors": [{"script": "nope.py", "runtime": "python-reference"}]}
    with pytest.raises(DecisionInspectorConfigError):
        load_decision_inspectors(cfg, base_dir=tmp_path)


async def test_chokepoint_awaits_async_script_inspector(tmp_path) -> None:
    """End-to-end (#46): a script-backed (async) inspector registered on
    the PolicyContext is awaited + composed by the chokepoint, relaxing
    REQUIRE_APPROVAL → ALLOW. Proves the dormant layer is now live."""
    from capabledeputy.audit.writer import AuditWriter
    from capabledeputy.policy.engine import PolicyDecision
    from capabledeputy.session.graph import SessionGraph

    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    (insp,) = load_decision_inspectors(
        {"decision_inspectors": [{"source": _RELAX_SCRIPT, "runtime": "python-reference"}]},
    )
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(decision_inspectors=(insp,)),
        audit=audit,
        graph=graph,
    )
    proposed = PolicyDecision(decision=Decision.REQUIRE_APPROVAL, rule="base", reason="r")
    adjusted = await hooks.apply_decision_inspectors(
        None,
        object(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com"),
        "email.send",
        proposed,
    )
    assert adjusted.decision == Decision.ALLOW
    assert "self-test-relax" in (adjusted.rule or "")


async def test_chokepoint_refuses_relax_of_structural_floor(tmp_path) -> None:
    """Security guardrail (#41/#46): a script relax must NOT cross a
    structural DENY floor — it may only soften REQUIRE_APPROVAL to ALLOW."""
    from capabledeputy.audit.writer import AuditWriter
    from capabledeputy.policy.engine import PolicyDecision
    from capabledeputy.session.graph import SessionGraph

    # A greedy script that always tries to relax to ALLOW.
    greedy = (
        "def inspect(action, session, proposed_outcome):\n"
        '    return relax(to="allow", rule="greedy", rationale="always allow")\n'
    )
    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    (insp,) = load_decision_inspectors(
        {"decision_inspectors": [{"source": greedy, "runtime": "python-reference"}]},
    )
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(decision_inspectors=(insp,)),
        audit=audit,
        graph=graph,
    )
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com")

    # DENY base ⇒ relax refused, stays DENY.
    denied = await hooks.apply_decision_inspectors(
        None,
        object(),
        action,
        "email.send",
        PolicyDecision(decision=Decision.DENY, rule="blp-floor"),
    )
    assert denied.decision == Decision.DENY

    # OVERRIDE_REQUIRED base ⇒ also refused.
    over = await hooks.apply_decision_inspectors(
        None,
        object(),
        action,
        "email.send",
        PolicyDecision(decision=Decision.OVERRIDE_REQUIRED, rule="override-floor"),
    )
    assert over.decision == Decision.OVERRIDE_REQUIRED

    # REQUIRE_APPROVAL base ⇒ the legitimate relax goes through.
    relaxed = await hooks.apply_decision_inspectors(
        None,
        object(),
        action,
        "email.send",
        PolicyDecision(decision=Decision.REQUIRE_APPROVAL, rule="approval"),
    )
    assert relaxed.decision == Decision.ALLOW


async def test_chokepoint_survives_buggy_script(tmp_path) -> None:
    """A script that errors at evaluation must be caught + audited as
    abstain, never crash the chokepoint (fail-safe at runtime)."""
    from capabledeputy.audit.writer import AuditWriter
    from capabledeputy.policy.engine import PolicyDecision
    from capabledeputy.session.graph import SessionGraph

    # `inspect` raises (references an undefined name) at evaluation.
    boom = "def inspect(action, session, proposed_outcome):\n    return undefined_name\n"
    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    (insp,) = load_decision_inspectors(
        {"decision_inspectors": [{"source": boom, "runtime": "python-reference"}]},
    )
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(decision_inspectors=(insp,)),
        audit=audit,
        graph=graph,
    )
    proposed = PolicyDecision(decision=Decision.REQUIRE_APPROVAL, rule="base")
    adjusted = await hooks.apply_decision_inspectors(
        None,
        object(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com"),
        "email.send",
        proposed,
    )
    # Unchanged — the buggy inspector abstained (its error was audited).
    assert adjusted.decision == Decision.REQUIRE_APPROVAL


def _starlark_available() -> bool:
    try:
        import starlark  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _starlark_available(), reason="starlark extra not installed")
async def test_real_starlark_host_relaxes() -> None:
    """The real sandboxed runtime (the production boundary) relaxes via
    the same loader path — guarded so CI without the extra still runs."""
    (insp,) = load_decision_inspectors(
        {"decision_inspectors": [{"source": _RELAX_SCRIPT, "runtime": "starlark"}]},
    )
    out = await insp.inspect(
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com"),
        session=object(),
        proposed_outcome=_proposed(Decision.REQUIRE_APPROVAL),
    )
    assert isinstance(out, DecisionRelax)
    assert out.to == Decision.ALLOW


@pytest.mark.skipif(not _starlark_available(), reason="starlark extra not installed")
def test_shipped_starter_scripts_compile() -> None:
    """The starter library (#47) must stay loadable under the real
    Starlark runtime so a fresh install can adopt it as-is."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    policies = repo / "configs" / "policies"
    scripts = sorted(policies.glob("*.star"))
    assert scripts, "expected shipped starter .star scripts"
    cfg = {
        "decision_inspectors": [{"script": s.name, "runtime": "starlark"} for s in scripts],
    }
    inspectors = load_decision_inspectors(cfg, base_dir=policies)
    assert len(inspectors) == len(scripts)


@pytest.mark.skipif(not _starlark_available(), reason="starlark extra not installed")
async def test_sensitive_egress_confirm_tightens() -> None:
    """The shipped tightener adds approval to an auto-allowed egress while
    the session carries restricted data."""
    from pathlib import Path

    from capabledeputy.policy.labels import CategoryTag, LabelState
    from capabledeputy.policy.tiers import Tier

    repo = Path(__file__).resolve().parents[2]
    (insp,) = load_decision_inspectors(
        {"decision_inspectors": [{"script": "sensitive_egress_confirm.star"}]},
        base_dir=repo / "configs" / "policies",
    )

    class _S:
        label_state = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))

    out = await insp.inspect(
        action=Action(kind=CapabilityKind.SEND_EMAIL, target="x@y.com"),
        session=_S(),
        proposed_outcome=_proposed(Decision.ALLOW),
    )
    assert isinstance(out, DecisionTighten)
    assert out.to == Decision.REQUIRE_APPROVAL
