"""Tests for the policy.preview tool — agent-callable dry-run."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolContext


@pytest.fixture
async def app(tmp_path: Path) -> App:
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await a.startup()
    return a


async def test_preview_allow_for_unblocked_action(app: App) -> None:
    tool = app.registry.get("policy.preview")
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    result = await tool.handler(
        {"kind": "READ_FS", "target": "/notes"},
        ToolContext(session_id=s.id, label_set=frozenset()),
    )
    assert result.output["decision"] == "allow"
    assert result.output["would_match_capability"] is True


async def test_preview_deny_for_blocked_egress(app: App) -> None:
    """The lethal-trifecta case: untrusted-meets-egress predicted as DENY."""
    tool = app.registry.get("policy.preview")
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    tainted = replace(
        s,
        capability_set=frozenset({cap}),
        label_set=frozenset({Label.UNTRUSTED_EXTERNAL}),
    )
    app.graph._sessions[s.id] = tainted

    result = await tool.handler(
        {"kind": "SEND_EMAIL", "target": "alice@example.com"},
        ToolContext(session_id=s.id, label_set=tainted.label_set),
    )
    assert result.output["decision"] == "deny"
    assert result.output["rule"] == "untrusted-meets-egress"


async def test_preview_does_not_mutate_session(app: App) -> None:
    """policy.preview is read-only; calling it must not add labels or
    record kinds in used_kinds."""
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    client = LabeledToolClient(app.registry, app.graph, app.audit)
    await client.call_tool(
        s.id, "policy.preview", {"kind": "SEND_EMAIL", "target": "x@y"},
    )
    after = app.graph.get(s.id)
    assert after.label_set == frozenset()
    # READ_FS gets recorded (it IS a real dispatch, gated by READ_FS),
    # but no foreign kinds leak in.
    assert after.used_kinds == frozenset({CapabilityKind.READ_FS})


async def test_preview_unknown_kind_returns_error(app: App) -> None:
    tool = app.registry.get("policy.preview")
    s = await app.graph.new()
    result = await tool.handler(
        {"kind": "NOPE", "target": "x"},
        ToolContext(session_id=s.id, label_set=frozenset()),
    )
    assert "error" in result.output
