"""Tests for the policy.preview registration toggle.

Default: registered (better agent planning). --no-policy-preview /
CAPDEP_POLICY_PREVIEW=0: not registered. Disabling it must NOT change
enforcement — a tainted egress still denies deterministically with no
preview tool present at all.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.tools.registry import ToolNotFoundError


@pytest.fixture
async def _started(tmp_path: Path):
    async def make(enable: bool) -> App:
        a = App(
            state_db_path=tmp_path / f"s-{enable}.db",
            audit_log_path=tmp_path / f"a-{enable}.jsonl",
            enable_policy_preview=enable,
        )
        await a.startup()
        return a

    return make


async def test_default_registers_policy_preview(_started) -> None:
    app = await _started(True)
    tool = app.registry.get("policy.preview")
    assert tool.name == "policy.preview"


async def test_disabled_does_not_register_policy_preview(_started) -> None:
    app = await _started(False)
    with pytest.raises(ToolNotFoundError):
        app.registry.get("policy.preview")
    # Other native tools are unaffected.
    assert app.registry.get("email.send").name == "email.send"


async def test_enforcement_unchanged_when_preview_disabled(_started) -> None:
    """The whole point: disabling preview is NOT a security control.
    A tainted session still denies egress deterministically with the
    preview tool entirely absent."""
    app = await _started(False)
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({cap}),
        label_set=frozenset({Label.UNTRUSTED_EXTERNAL}),
    )
    outcome = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "x@y.com", "subject": "s", "body": "b"},
    )
    assert outcome.decision.value == "deny"
    assert outcome.rule == "untrusted-meets-egress"
