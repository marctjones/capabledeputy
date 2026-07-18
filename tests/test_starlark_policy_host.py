"""#13 — StarlarkScriptHost: the real sandboxed policy-script runtime.

Starlark (via starlark-pyo3) is a *language-level* sandbox: a policy
script sees only the injected action/session/proposed_outcome dicts and
the relax/tighten/abstain helpers — no imports, no builtins, no I/O. These
tests prove the policy-language contract holds AND that the language
isolation actually blocks escape attempts the AST-filtered Python host
could only best-effort guard.

Skipped when the optional `capabledeputy[starlark]` extra isn't installed.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("starlark", reason="requires the capabledeputy[starlark] extra")

from capabledeputy.substrate.policy_script_host import (
    SafePythonScriptHost,
    StarlarkScriptHost,
    get_script_host,
)

_AUTO_READ = """
def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] == "require_approval" and action["kind"] == "READ_FS":
        return relax(to="allow", rule="auto-read", rationale="reads are safe")
    if action["kind"] == "SEND_EMAIL":
        return tighten(to="deny", rule="no-send", rationale="locked down")
    return abstain()
"""


async def test_compile_reports_starlark_runtime() -> None:
    script = StarlarkScriptHost().compile("auto_read", _AUTO_READ)
    assert script.runtime_kind == "starlark"


async def test_relax_tighten_abstain_contract() -> None:
    host = StarlarkScriptHost()
    script = host.compile("auto_read", _AUTO_READ)

    relaxed = await host.evaluate(
        script,
        action={"kind": "READ_FS"},
        session={},
        proposed_outcome={"decision": "require_approval"},
    )
    assert relaxed.kind == "relax"
    assert relaxed.to_decision == "allow"
    assert relaxed.rule == "auto-read"

    tightened = await host.evaluate(
        script,
        action={"kind": "SEND_EMAIL"},
        session={},
        proposed_outcome={"decision": "allow"},
    )
    assert tightened.kind == "tighten"
    assert tightened.to_decision == "deny"

    abstained = await host.evaluate(
        script,
        action={"kind": "READ_FS"},
        session={},
        proposed_outcome={"decision": "allow"},
    )
    assert abstained.kind == "abstain"


async def test_missing_inspect_rejected_at_compile() -> None:
    with pytest.raises(ValueError, match="must define"):
        StarlarkScriptHost().compile("bad", "x = 1\n")


async def test_syntax_error_rejected_at_compile() -> None:
    with pytest.raises(ValueError, match="Starlark"):
        StarlarkScriptHost().compile("bad", "def inspect(a, s, p):\n    return ???\n")


@pytest.mark.parametrize(
    "escape",
    [
        # Starlark's module loader — not available to policy scripts.
        "def inspect(a, s, p):\n    return load('os', 'x')\n",
        # Python builtins simply are not Starlark names.
        "def inspect(a, s, p):\n    return open('/etc/passwd')\n",
        "def inspect(a, s, p):\n    return __import__('os')\n",
    ],
)
async def test_language_isolation_blocks_escapes(escape: str) -> None:
    """No imports / file IO / builtins reachable — enforced by the
    Starlark language, caught at compile or surfaced as an error."""
    host = StarlarkScriptHost()
    try:
        script = host.compile("evil", escape)
    except ValueError:
        return  # blocked at parse/compile — good
    out = await host.evaluate(script, action={}, session={}, proposed_outcome={})
    assert out.kind == "error"  # blocked at eval — also good


async def test_non_dict_return_is_error() -> None:
    host = StarlarkScriptHost()
    script = host.compile("weird", "def inspect(a, s, p):\n    return 42\n")
    out = await host.evaluate(script, action={}, session={}, proposed_outcome={})
    assert out.kind == "error"
    assert "non-dict" in out.error


async def test_unknown_kind_is_error() -> None:
    host = StarlarkScriptHost()
    script = host.compile(
        "weird",
        'def inspect(a, s, p):\n    return {"kind": "nope"}\n',
    )
    out = await host.evaluate(script, action={}, session={}, proposed_outcome={})
    assert out.kind == "error"
    assert "unknown kind" in out.error


async def test_evaluation_timeout_kills_child_process() -> None:
    host = StarlarkScriptHost()
    script = host.compile(
        "slow",
        "def inspect(action, session, proposed_outcome):\n"
        '    return relax(to="allow", rule="slow", rationale="too slow")\n',
    )
    script = replace(script, timeout_seconds=0.001)
    out = await host.evaluate(script, action={}, session={}, proposed_outcome={})
    assert out.kind == "error"
    assert "exceeded timeout" in out.error


def test_factory_selects_host_and_fails_closed_on_unknown() -> None:
    assert get_script_host("starlark").runtime_kind == "starlark"
    assert isinstance(get_script_host("python-reference"), SafePythonScriptHost)
    with pytest.raises(ValueError, match="unknown policy-script runtime"):
        get_script_host("lua")


# --- #308 — adversarial contract hardening --------------------------------
#
# Complements the floor tests (which assume the host is sound) by attacking the
# operator-code boundary itself: hermeticity, resource limits, error→abstain
# (never fail-open), and monotonicity end-to-end through the chokepoint.


@pytest.mark.parametrize(
    "body",
    [
        # host-object / builtin reach — none are in scope in Starlark.
        'def inspect(a, s, p):\n    return open("/etc/passwd").read()\n',
        "def inspect(a, s, p):\n    return getattr(a, '__class__')\n",
        "def inspect(a, s, p):\n    return __import__('os').getcwd()\n",
        "def inspect(a, s, p):\n    return type(a)\n",
        "def inspect(a, s, p):\n    return eval('1+1')\n",
        # reference a global that was never injected.
        "def inspect(a, s, p):\n    return SECRET_HOST_STATE\n",
    ],
)
async def test_starlark_hermetic_no_host_escape(body: str) -> None:
    """A script reaching for I/O, imports, host objects, or un-injected globals
    is refused at compile OR yields an error at evaluation — never a usable
    relax/tighten (no fail-open escape)."""
    host = StarlarkScriptHost()
    try:
        script = host.compile("evil", body)
    except ValueError:
        return  # refused at compile — the boundary held
    out = await host.evaluate(
        script,
        action={"kind": "READ_FS"},
        session={},
        proposed_outcome={"decision": "require_approval"},
    )
    assert out.kind not in ("relax", "tighten"), f"escape produced {out.kind}"


async def test_starlark_runaway_loop_is_terminated() -> None:
    """A malicious unbounded loop does not hang the chokepoint — the killable
    child process is terminated by the wall-clock timeout and surfaces an error
    (never a hang, never fail-open)."""
    host = StarlarkScriptHost()
    script = host.compile(
        "runaway",
        "def inspect(action, session, proposed_outcome):\n"
        "    total = 0\n"
        "    for i in range(100000000):\n"
        "        total += i\n"
        '    return relax(to="allow", rule="r", rationale="")\n',
    )
    script = replace(script, timeout_seconds=0.2)
    out = await host.evaluate(script, action={}, session={}, proposed_outcome={})
    assert out.kind == "error"
    assert "exceeded timeout" in out.error


async def test_script_error_is_abstain_not_fail_open(tmp_path) -> None:
    """The money contract: a script that ERRORS at evaluation is caught,
    audited, and treated as ABSTAIN by the chokepoint — the base decision stands
    unchanged; it NEVER crashes the chokepoint and NEVER fails open."""
    from uuid import uuid4

    from capabledeputy.audit.events import EventType
    from capabledeputy.audit.writer import AuditWriter
    from capabledeputy.policy.actions import Action
    from capabledeputy.policy.capabilities import CapabilityKind
    from capabledeputy.policy.context import PolicyContext
    from capabledeputy.policy.decision_inspector_loader import ScriptDecisionInspector
    from capabledeputy.policy.engine import PolicyDecision
    from capabledeputy.policy.rules import Decision
    from capabledeputy.session.graph import SessionGraph
    from capabledeputy.session.model import Session
    from capabledeputy.tools.policy_hooks import ToolPolicyHooks

    host = StarlarkScriptHost()
    script = host.compile(
        "boom",
        'def inspect(action, session, proposed_outcome):\n    fail("boom")\n',
    )
    inspector = ScriptDecisionInspector("boom", host, script, failure_mode="abstain")

    audit = AuditWriter(tmp_path / "audit.jsonl")
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(decision_inspectors=(inspector,)),
        audit=audit,
        graph=SessionGraph(audit=audit),
    )
    base = PolicyDecision(decision=Decision.REQUIRE_APPROVAL, rule="base")
    adjusted = await hooks.apply_decision_inspectors(
        uuid4(),
        Session.new(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        "email.send",
        base,
    )
    # abstain: the base decision is unchanged (not relaxed to ALLOW).
    assert adjusted.decision == Decision.REQUIRE_APPROVAL
    events = await audit.read_all()
    assert any(e.event_type == EventType.POLICY_DECIDED for e in events)


async def test_script_relax_cannot_cross_a_floor(tmp_path) -> None:
    """A script that always relaxes to ALLOW cannot cross a DENY floor: driven
    through the real chokepoint against a DENY base, the floor stands and the
    relaxation is refused (ties operator scripts to the #306 guard)."""
    from uuid import uuid4

    from capabledeputy.audit.events import EventType
    from capabledeputy.audit.writer import AuditWriter
    from capabledeputy.policy.actions import Action
    from capabledeputy.policy.capabilities import CapabilityKind
    from capabledeputy.policy.context import PolicyContext
    from capabledeputy.policy.decision_inspector_loader import ScriptDecisionInspector
    from capabledeputy.policy.engine import PolicyDecision
    from capabledeputy.policy.rules import Decision
    from capabledeputy.session.graph import SessionGraph
    from capabledeputy.session.model import Session
    from capabledeputy.tools.policy_hooks import ToolPolicyHooks

    host = StarlarkScriptHost()
    script = host.compile(
        "always-allow",
        "def inspect(action, session, proposed_outcome):\n"
        '    return relax(to="allow", rule="yolo", rationale="always")\n',
    )
    inspector = ScriptDecisionInspector("always-allow", host, script)
    audit = AuditWriter(tmp_path / "audit.jsonl")
    hooks = ToolPolicyHooks(
        policy_context=PolicyContext(decision_inspectors=(inspector,)),
        audit=audit,
        graph=SessionGraph(audit=audit),
    )
    adjusted = await hooks.apply_decision_inspectors(
        uuid4(),
        Session.new(),
        Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com"),
        "email.send",
        PolicyDecision(decision=Decision.DENY, rule="structural-floor"),
    )
    assert adjusted.decision == Decision.DENY
    events = await audit.read_all()
    assert any(e.event_type == EventType.RELAXATION_REFUSED for e in events)
