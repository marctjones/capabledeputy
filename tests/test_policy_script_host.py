"""Tests for the PolicyScriptHost port + SafePythonScriptHost reference impl.

These tests verify the script-language contract operators see, not
the security boundary. For real sandboxing use Starlark/Wasm.
"""

from __future__ import annotations

import pytest

from capabledeputy.substrate.policy_script_host import (
    SafePythonScriptHost,
)


@pytest.fixture
def host() -> SafePythonScriptHost:
    return SafePythonScriptHost()


def test_compile_rejects_missing_inspect(host: SafePythonScriptHost) -> None:
    with pytest.raises(ValueError, match="must define `def inspect"):
        host.compile("bad", "x = 1")


def test_compile_rejects_import(host: SafePythonScriptHost) -> None:
    src = """
import os
def inspect(action, session, proposed_outcome):
    return None
"""
    with pytest.raises(ValueError, match="may not use `import`"):
        host.compile("bad", src)


def test_compile_rejects_eval_call(host: SafePythonScriptHost) -> None:
    src = """
def inspect(action, session, proposed_outcome):
    return eval("1+1")
"""
    with pytest.raises(ValueError, match="may not call 'eval'"):
        host.compile("bad", src)


def test_compile_rejects_open_call(host: SafePythonScriptHost) -> None:
    src = """
def inspect(action, session, proposed_outcome):
    return open("/etc/passwd").read()
"""
    with pytest.raises(ValueError, match="may not call 'open'"):
        host.compile("bad", src)


def test_compile_rejects_syntax_error(host: SafePythonScriptHost) -> None:
    with pytest.raises(ValueError, match="syntax error"):
        host.compile("bad", "def inspect( bad syntax")


def test_compile_accepts_valid_script(host: SafePythonScriptHost) -> None:
    src = """
def inspect(action, session, proposed_outcome):
    return None
"""
    script = host.compile("good", src)
    assert script.name == "good"
    assert script.runtime_kind == "python-reference"


@pytest.mark.asyncio
async def test_evaluate_abstain(host: SafePythonScriptHost) -> None:
    src = """
def inspect(action, session, proposed_outcome):
    return None
"""
    script = host.compile("abstain", src)
    outcome = await host.evaluate(
        script,
        action={"kind": "READ_FS"},
        session={"labels": []},
        proposed_outcome={"decision": "allow"},
    )
    assert outcome.kind == "abstain"


@pytest.mark.asyncio
async def test_evaluate_relax(host: SafePythonScriptHost) -> None:
    src = """
def inspect(action, session, proposed_outcome):
    if action.get("target") == "self@example.com":
        return relax(to="allow", rule="self-egress", rationale="self-correspondence")
    return None
"""
    script = host.compile("relax", src)
    outcome = await host.evaluate(
        script,
        action={"kind": "SEND_EMAIL", "target": "self@example.com"},
        session={},
        proposed_outcome={"decision": "require_approval"},
    )
    assert outcome.kind == "relax"
    assert outcome.to_decision == "allow"
    assert outcome.rule == "self-egress"


@pytest.mark.asyncio
async def test_evaluate_tighten(host: SafePythonScriptHost) -> None:
    src = """
def inspect(action, session, proposed_outcome):
    if action.get("kind") == "QUEUE_PURCHASE":
        return tighten(to="deny", rule="purchases-forbidden", rationale="no purchases")
    return None
"""
    script = host.compile("tighten", src)
    outcome = await host.evaluate(
        script,
        action={"kind": "QUEUE_PURCHASE"},
        session={},
        proposed_outcome={"decision": "allow"},
    )
    assert outcome.kind == "tighten"
    assert outcome.to_decision == "deny"
    assert outcome.rule == "purchases-forbidden"


@pytest.mark.asyncio
async def test_evaluate_step_limit_enforced(host: SafePythonScriptHost) -> None:
    """Infinite loop terminates at the step limit, doesn't hang."""
    src = """
def inspect(action, session, proposed_outcome):
    while True:
        x = 1
"""
    script = host.compile("loop", src)
    # Reduce step limit so the test is fast
    from dataclasses import replace as _dc_replace

    script = _dc_replace(script, step_limit=100)
    outcome = await host.evaluate(
        script,
        action={},
        session={},
        proposed_outcome={},
    )
    assert outcome.kind == "error"
    assert "step limit" in outcome.error.lower()


@pytest.mark.asyncio
async def test_evaluate_unknown_kind_is_error(host: SafePythonScriptHost) -> None:
    src = """
def inspect(action, session, proposed_outcome):
    return {"kind": "explode"}
"""
    script = host.compile("weird", src)
    outcome = await host.evaluate(
        script,
        action={},
        session={},
        proposed_outcome={},
    )
    assert outcome.kind == "error"
    assert "unknown kind" in outcome.error


@pytest.mark.asyncio
async def test_evaluate_non_dict_return_is_error(host: SafePythonScriptHost) -> None:
    src = """
def inspect(action, session, proposed_outcome):
    return 42
"""
    script = host.compile("bad-return", src)
    outcome = await host.evaluate(
        script,
        action={},
        session={},
        proposed_outcome={},
    )
    assert outcome.kind == "error"
    assert "non-dict" in outcome.error


@pytest.mark.asyncio
async def test_evaluate_runtime_error_captured(host: SafePythonScriptHost) -> None:
    src = """
def inspect(action, session, proposed_outcome):
    return action["nope"]  # KeyError
"""
    script = host.compile("err", src)
    outcome = await host.evaluate(
        script,
        action={},
        session={},
        proposed_outcome={},
    )
    assert outcome.kind == "error"
    assert "nope" in outcome.error
