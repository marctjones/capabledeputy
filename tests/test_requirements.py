"""#307 — operator requirement DSL: load, verify, and fail-closed enforcement.

Covers: the built-in hard requirements hold for the three shipped presets; the
checker is faithful (it catches a posture / inspector configuration that
violates a requirement — including a relaxer that would open a silent egress
path); loader fail-closed behavior; and the daemon-start enforcement gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.posture import BUILTIN_POSTURES, Posture
from capabledeputy.policy.requirements import (
    BUILTIN_REQUIREMENTS,
    Requirement,
    RequirementError,
    RequirementKind,
    RequirementViolationError,
    enforce_requirements,
    load_requirements,
    verify_requirements,
)
from capabledeputy.substrate.decision_inspectors_builtin import SelfEgressRelaxer


def _unmet(results):
    return [r.requirement.id for r in results if not r.satisfied]


# --- built-in requirements hold for every shipped preset ------------------


@pytest.mark.parametrize("pid", sorted(BUILTIN_POSTURES))
def test_builtins_satisfied_by_every_preset(pid: str) -> None:
    """All three shipped presets satisfy every built-in hard requirement — the
    #307 counterpart to #306's cross-preset floor equality."""
    results = verify_requirements(posture=BUILTIN_POSTURES[pid])
    assert _unmet(results) == [], _unmet(results)
    assert len(results) == len(BUILTIN_REQUIREMENTS)


def test_builtins_satisfied_with_preset_default_inspectors() -> None:
    """A preset's inspector_set, auto-instantiated with safe (empty) defaults,
    does not violate any built-in — the self-egress relaxer with no configured
    self-addresses relaxes nothing."""
    from capabledeputy.policy.decision_inspector_loader import (
        select_inspectors_for_posture,
    )

    hsu = BUILTIN_POSTURES["high-security-useful"]
    inspectors, _warnings = select_inspectors_for_posture((), hsu.inspector_set)
    results = verify_requirements(posture=hsu, decision_inspectors=inspectors)
    assert _unmet(results) == []


# --- the checker is faithful: it catches real violations ------------------


def test_projection_only_false_passes_builtin_but_fails_optin() -> None:
    """A posture with projection_only=False re-exposes untrusted-source raw
    readers in TURN_LEVEL only — the UNCONDITIONAL builtin (exposure-limited-mode
    hiding) still holds, so it must PASS; the OPT-IN planner-blind requirement is
    the one that fails. The knob is a designed operator override, not a floor
    breach, so it must not block start unless the operator opted into the stance."""
    bad = Posture(id="raw-open", projection_only=False).validate()

    builtin_results = verify_requirements(posture=bad)
    assert "builtin.untrusted-source-exposure-floor" not in _unmet(builtin_results)

    optin = Requirement(
        id="op.planner-blind",
        kind=RequirementKind.PLANNER_BLIND_TO_UNTRUSTED_SOURCE,
        description="planner blind",
    )
    with_optin = verify_requirements(posture=bad, custom=(optin,))
    assert "op.planner-blind" in _unmet(with_optin)

    # And a projection_only=True posture satisfies the opt-in.
    good = verify_requirements(posture=BUILTIN_POSTURES["strict"], custom=(optin,))
    assert "op.planner-blind" not in _unmet(good)


def test_self_egress_relaxer_violates_never_silent_egress() -> None:
    """A SelfEgressRelaxer configured with a self-address would relax a
    confidential email from REQUIRE_APPROVAL to ALLOW — the DSL catches it as a
    violation of a `never_silent_egress` requirement (checked against the
    EFFECTIVE, post-inspector decision)."""
    req = Requirement(
        id="op.creds-no-egress",
        kind=RequirementKind.NEVER_SILENT_EGRESS,
        category="credentials",
        description="credentials never silently egress",
    )
    strict = BUILTIN_POSTURES["strict"]

    # Clean (no relaxer) — satisfied: credentials email is REQUIRE_APPROVAL.
    clean = verify_requirements(posture=strict, custom=(req,))
    assert "op.creds-no-egress" not in _unmet(clean)

    # With a self-egress relaxer scoped to me@example.com — violated.
    relaxer = (SelfEgressRelaxer(self_addresses=frozenset({"me@example.com"})),)
    dirty = verify_requirements(posture=strict, decision_inspectors=relaxer, custom=(req,))
    assert "op.creds-no-egress" in _unmet(dirty)


def test_deny_egress_requirement_over_financial_purchase() -> None:
    """A custom deny_egress(financial, [queue_purchase]) holds — financial +
    purchase is DENY (conflict REQUIRE_APPROVAL + irreversible-commerce DENY,
    most-restrictive)."""
    req = Requirement(
        id="op.fin-deny-purchase",
        kind=RequirementKind.DENY_EGRESS,
        category="financial",
        channels=(CapabilityKind.QUEUE_PURCHASE,),
        description="financial never purchases",
    )
    results = verify_requirements(posture=BUILTIN_POSTURES["strict"], custom=(req,))
    assert "op.fin-deny-purchase" not in _unmet(results)


def test_deny_egress_requirement_that_engine_does_not_meet_is_flagged() -> None:
    """An operator requirement the engine does NOT satisfy (credentials hard-DENY
    on email — the engine only REQUIRE_APPROVALs that channel) is reported unmet,
    not silently accepted. Fidelity, not wishful thinking."""
    req = Requirement(
        id="op.creds-deny-email",
        kind=RequirementKind.DENY_EGRESS,
        category="credentials",
        channels=(CapabilityKind.SEND_EMAIL,),
        description="credentials hard-denied on email (engine only require-approvals)",
    )
    results = verify_requirements(posture=BUILTIN_POSTURES["strict"], custom=(req,))
    assert "op.creds-deny-email" in _unmet(results)


def test_health_financial_deny_are_not_non_negotiable_builtins() -> None:
    """Health/financial DENY-egress are deliberately NOT built-in: the engine's
    personal-crossing path can suppress them, so a non-negotiable builtin would
    overstate the guarantee (the false-pass the DSL must avoid)."""
    ids = {r.id for r in BUILTIN_REQUIREMENTS}
    assert ids == {
        "builtin.untrusted-never-egress",
        "builtin.untrusted-source-exposure-floor",
        "builtin.restricted-mode-floor",
    }


def test_personal_crossing_is_seen_by_the_checker_no_false_pass() -> None:
    """A health-deny custom requirement is correctly reported UNMET when the
    deployment runs a `personal` profile with a human-ratified rule that crosses
    the health floor — the checker threads trust_profile_is_personal + rules_v2,
    so it observes the ALLOW the runtime would produce (no silent false-pass)."""
    from capabledeputy.policy.decision_rules import (
        DecisionRule,
        DecisionRules,
        RuleOutcome,
        RulePredicate,
    )

    health_deny = Requirement(
        id="op.health-deny-egress",
        kind=RequirementKind.DENY_EGRESS,
        category="health",
        channels=(CapabilityKind.SEND_EMAIL,),
        description="health hard-denied on email",
    )
    crossing = DecisionRules(
        rules=(
            DecisionRule(
                rule_id="op-crosses-health",
                predicate=RulePredicate(axis_a_category="health", effect_class="social.send_email"),
                outcome=RuleOutcome.AUTO,
                rationale="operator crosses their own health floor",
                human_ratified_by="owner",
                crosses_floor="health-meets-egress",
            ),
        ),
    )
    strict = BUILTIN_POSTURES["strict"]

    # Managed / no crossing rule ⇒ the requirement holds.
    managed = verify_requirements(posture=strict, custom=(health_deny,))
    assert "op.health-deny-egress" not in _unmet(managed)

    # Personal profile WITH the ratified crossing rule ⇒ the checker sees the
    # ALLOW and reports the requirement UNMET (the false-pass is closed).
    personal = verify_requirements(
        posture=strict,
        custom=(health_deny,),
        trust_profile_is_personal=True,
        rules_v2=crossing,
    )
    assert "op.health-deny-egress" in _unmet(personal)


# --- enforcement gate -----------------------------------------------------


def test_enforce_raises_on_violation() -> None:
    """A projection_only=False posture that ALSO declares the opt-in planner-blind
    requirement refuses start. (No valid posture fails a builtin — those are
    unconditional tripwires — so a violation comes from a custom requirement.)"""
    bad = Posture(id="raw-open", projection_only=False).validate()
    optin = Requirement(
        id="op.planner-blind",
        kind=RequirementKind.PLANNER_BLIND_TO_UNTRUSTED_SOURCE,
        description="planner blind",
    )
    with pytest.raises(RequirementViolationError, match="planner-blind"):
        enforce_requirements(posture=bad, custom=(optin,))


def test_enforce_passes_and_returns_log_line() -> None:
    messages = enforce_requirements(posture=BUILTIN_POSTURES["strict"])
    assert len(messages) == 1
    assert "requirement(s) satisfied" in messages[0]


# --- YAML loader ----------------------------------------------------------


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_load_requirements_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RequirementError, match="missing"):
        load_requirements(tmp_path / "nope.yaml")


def test_load_requirements_empty_yields_empty(tmp_path: Path) -> None:
    assert load_requirements(_write(tmp_path / "r.yaml", "requirements: []\n")) == ()
    assert load_requirements(_write(tmp_path / "e.yaml", "\n")) == ()


def test_load_requirements_roundtrip(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "requirements.yaml",
        """
requirements:
  - id: op.a
    kind: never_silent_egress
    category: credentials
    description: creds
  - id: op.b
    kind: deny_egress
    category: financial
    channels: [send_email, queue_purchase]
    description: fin
""",
    )
    reqs = load_requirements(p)
    assert {r.id for r in reqs} == {"op.a", "op.b"}
    b = next(r for r in reqs if r.id == "op.b")
    assert b.channels == (CapabilityKind.SEND_EMAIL, CapabilityKind.QUEUE_PURCHASE)


def test_load_requirements_category_kind_requires_category(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "r.yaml",
        "requirements:\n  - id: op.x\n    kind: never_silent_egress\n",
    )
    with pytest.raises(RequirementError, match="requires a 'category'"):
        load_requirements(p)


def test_load_requirements_deny_egress_requires_channels(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "r.yaml",
        "requirements:\n  - id: op.x\n    kind: deny_egress\n    category: health\n",
    )
    with pytest.raises(RequirementError, match="requires 'channels'"):
        load_requirements(p)


def test_load_requirements_unknown_kind_fails_closed(tmp_path: Path) -> None:
    p = _write(tmp_path / "r.yaml", "requirements:\n  - id: op.x\n    kind: bogus\n")
    with pytest.raises(RequirementError):
        load_requirements(p)


def test_load_requirements_unknown_channel_fails_closed(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "r.yaml",
        "requirements:\n  - id: op.x\n    kind: deny_egress\n    category: health\n"
        "    channels: [teleport]\n",
    )
    with pytest.raises(RequirementError, match="unknown channel"):
        load_requirements(p)


def test_load_requirements_rejects_builtin_id_shadow(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "r.yaml",
        "requirements:\n  - id: builtin.untrusted-never-egress\n    kind: untrusted_never_egress\n",
    )
    with pytest.raises(RequirementError, match="built-in id"):
        load_requirements(p)


# --- daemon-start enforcement glue ----------------------------------------


def test_enforce_from_config_noop_without_active_posture(tmp_path: Path) -> None:
    """The requirement DSL is posture-scoped: no active posture ⇒ no-op, so an
    unconfigured legacy runtime keeps its prior startup behavior."""
    from types import SimpleNamespace

    from capabledeputy.daemon.lifecycle import enforce_requirements_from_config

    ctx = SimpleNamespace(active_posture=None, decision_inspectors=(), clearance_max_tier=None)
    assert enforce_requirements_from_config(ctx, configs_dir=tmp_path) == []


def test_enforce_from_config_raises_on_custom_requirement_violation(tmp_path: Path) -> None:
    """A custom requirements.yaml the active posture+inspectors violate refuses
    daemon start — the end-to-end #307 gate."""
    from types import SimpleNamespace

    from capabledeputy.daemon.lifecycle import enforce_requirements_from_config

    (tmp_path / "requirements.yaml").write_text(
        "requirements:\n  - id: op.creds-no-egress\n    kind: never_silent_egress\n"
        "    category: credentials\n    description: creds\n",
        encoding="utf-8",
    )
    ctx = SimpleNamespace(
        active_posture=BUILTIN_POSTURES["strict"],
        decision_inspectors=(SelfEgressRelaxer(self_addresses=frozenset({"me@example.com"})),),
        clearance_max_tier=None,
    )
    with pytest.raises(RequirementViolationError, match="creds-no-egress"):
        enforce_requirements_from_config(ctx, configs_dir=tmp_path)


def test_shipped_example_file_loads_and_holds_for_presets() -> None:
    """The shipped worked-example requirements file parses and every custom
    requirement it declares is satisfied by all three presets (with their
    default inspectors) — so it is a valid, honest example."""
    from capabledeputy.policy.decision_inspector_loader import (
        select_inspectors_for_posture,
    )

    example = Path("configs/requirements.example.yaml")
    custom = load_requirements(example)
    assert custom  # non-empty worked examples
    for pid, posture in BUILTIN_POSTURES.items():
        inspectors, _ = select_inspectors_for_posture((), posture.inspector_set)
        results = verify_requirements(
            posture=posture,
            decision_inspectors=inspectors,
            custom=custom,
        )
        assert _unmet(results) == [], f"{pid}: {_unmet(results)}"
