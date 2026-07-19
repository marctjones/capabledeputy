"""Tests for the recovery-step synthesizer (Issue #3).

Locks in the deterministic mapping from policy-decision rule to
slash-command sequence. Adding a new rule? Add a golden test here.
"""

from __future__ import annotations

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import _synthesize_recovery_steps
from capabledeputy.policy.rules import Decision


def _act(
    kind: CapabilityKind = CapabilityKind.SEND_EMAIL,
    target: str = "marc@joneslaw.io",
) -> Action:
    return Action(kind=kind, target=target)


def test_allow_produces_no_recovery() -> None:
    steps = _synthesize_recovery_steps(
        decision=Decision.ALLOW,
        rule="some-rule",
        action=_act(),
        effect_class=None,
    )
    assert steps == ()


def test_missing_rule_produces_no_recovery() -> None:
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule=None,
        action=_act(),
        effect_class=None,
    )
    assert steps == ()


def test_no_matching_capability_emits_grant() -> None:
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule="no-matching-capability",
        action=_act(),
        effect_class=None,
    )
    assert len(steps) == 1
    s = steps[0]
    assert s.command == "/grant"
    assert "SEND_EMAIL" in s.args
    assert "marc@joneslaw.io" in s.args
    assert "--one-shot" in s.args


def test_capability_expired_includes_ttl() -> None:
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule="capability-expired",
        action=_act(),
        effect_class=None,
    )
    assert len(steps) == 1
    assert "--ttl" in steps[0].args


def test_destructive_op_emits_destructive_flag() -> None:
    steps = _synthesize_recovery_steps(
        decision=Decision.REQUIRE_APPROVAL,
        rule="destructive-op-needs-approval",
        action=_act(kind=CapabilityKind.DELETE_FS, target="/tmp/x"),
        effect_class=None,
    )
    assert len(steps) == 1
    assert "--destructive" in steps[0].args


def test_rate_limit_emits_rate_flag() -> None:
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule="rate-limit-exceeded",
        action=_act(),
        effect_class=None,
    )
    assert len(steps) == 1
    assert "--rate" in steps[0].args


def test_revoked_by_prior_use_emits_spawn_then_grant() -> None:
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule="capability-revoked-by-prior-use",
        action=_act(),
        effect_class=None,
    )
    assert len(steps) == 2
    assert steps[0].command == "/spawn"
    assert steps[1].command == "/grant"


def test_untrusted_meets_egress_emits_three_alternatives() -> None:
    """The classic label-conflict denial: three recovery paths
    (spawn-clean primary; override alternative)."""
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule="untrusted-meets-egress",
        action=_act(),
        effect_class="egress.email",
    )
    assert len(steps) == 3
    commands = [s.command for s in steps]
    assert commands == ["/spawn", "/grant", "/override"]


def test_financial_meets_email_emits_recovery() -> None:
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule="financial-meets-email",
        action=_act(),
        effect_class="egress.email",
    )
    assert len(steps) >= 1
    assert steps[0].command == "/spawn"


def test_sandbox_no_actuator_emits_nothing() -> None:
    """No slash-command recovery — operator must wire substrate."""
    steps = _synthesize_recovery_steps(
        decision=Decision.OVERRIDE_REQUIRED,
        rule="sandbox-no-actuator",
        action=_act(kind=CapabilityKind.EXECUTE_SANDBOX, target="scratch"),
        effect_class="EXECUTE.sandbox",
    )
    assert steps == ()


def test_unknown_rule_emits_nothing() -> None:
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule="some-rule-we-have-not-mapped-yet",
        action=_act(),
        effect_class=None,
    )
    assert steps == ()


def test_recovery_step_as_command_line() -> None:
    """Round-trip a step to its literal pasteable form."""
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule="no-matching-capability",
        action=_act(),
        effect_class=None,
    )
    line = steps[0].as_command_line()
    assert line.startswith("/grant ")
    assert "SEND_EMAIL" in line
    assert "marc@joneslaw.io" in line
    assert "--one-shot" in line


# --- End-to-end via decide() -------------------------------------------------


def test_decide_populates_recovery_steps_on_deny() -> None:
    """The public decide() wrapper auto-populates recovery_steps on
    non-ALLOW outcomes by calling the synthesizer."""
    from capabledeputy.policy.engine import decide

    # No capabilities at all → no-matching-capability rule fires
    result = decide(
        frozenset(),
        _act(),
    )
    assert result.decision == Decision.DENY
    assert result.recovery_steps, (
        f"expected recovery_steps populated; got {result.recovery_steps!r}"
    )
    assert result.recovery_steps[0].command == "/grant"


def test_decide_allow_has_no_recovery_steps() -> None:
    from capabledeputy.policy.capabilities import Capability
    from capabledeputy.policy.engine import decide

    result = decide(
        frozenset({Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")}),
        _act(),
    )
    assert result.decision == Decision.ALLOW
    assert result.recovery_steps == ()


def test_read_fs_grant_widens_to_directory() -> None:
    # #421 — the daemon offers a READ_FS grant scoped to the file's directory.
    steps = _synthesize_recovery_steps(
        decision=Decision.DENY,
        rule="no-matching-capability",
        action=_act(kind=CapabilityKind.READ_FS, target="/tmp/foo/bar.txt"),
        effect_class=None,
    )
    assert steps[0].command == "/grant"
    assert "READ_FS" in steps[0].args
    assert "/tmp/foo/*" in steps[0].args  # directory subtree, not the single file
    # The rationale still names the original file that was denied.
    assert "/tmp/foo/bar.txt" in steps[0].rationale


def test_widen_read_fs_grant_target_cases() -> None:
    from capabledeputy.policy.engine import _widen_read_fs_grant_target as widen

    # A file → its parent directory subtree.
    assert widen("READ_FS", "/tmp/foo/bar.txt") == "/tmp/foo/*"
    # A directory (no file extension) → its own subtree.
    assert widen("READ_FS", "/Volumes/External") == "/Volumes/External/*"
    assert widen("READ_FS", "/Volumes/External/") == "/Volumes/External/*"
    # A dotfile is treated as a directory-ish leaf (not a widenable file).
    assert widen("READ_FS", "/home/u/.config") == "/home/u/.config/*"
    # Already-glob targets are left as-is.
    assert widen("READ_FS", "*") == "*"
    assert widen("READ_FS", "/a/b/*") == "/a/b/*"
    assert widen("READ_FS", "/a/b/**") == "/a/b/**"
    # Non-READ_FS kinds are never widened.
    assert widen("SEND_EMAIL", "dad@example.com") == "dad@example.com"
