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
