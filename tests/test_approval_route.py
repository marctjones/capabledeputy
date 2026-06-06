"""Tests for the declarative ApprovalRoute.

Covers route resolution for all three payload strategies, plus the
end-to-end wiring: a REQUIRE_APPROVAL outcome from LabeledToolClient
carries a ready-to-submit `approval_submission` derived from the
tool's declared route — no hardcoded per-tool table.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.route import ApprovalPayloadKind, ApprovalRoute
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState, Tier
from capabledeputy.tools.client import LabeledToolClient


def test_body_arg_route_resolves_named_arg_as_payload() -> None:
    route = ApprovalRoute(
        action=ApprovalAction.SEND_EMAIL,
        target_arg="to",
        payload_kind=ApprovalPayloadKind.BODY_ARG,
        payload_arg="body",
    )
    out = route.resolve(
        "email.send",
        {"to": "x@y.com", "subject": "s", "body": "hello"},
        "agent wants to email",
    )
    assert out["action"] == "SEND_EMAIL"
    assert out["target"] == "x@y.com"
    assert out["payload"] == "hello"
    assert out["justification"] == "agent wants to email"


def test_json_args_route_serializes_all_args() -> None:
    route = ApprovalRoute(
        action=ApprovalAction.QUEUE_PURCHASE,
        target_arg="vendor",
        payload_kind=ApprovalPayloadKind.JSON_ARGS,
    )
    args = {"vendor": "amazon", "item": "towels", "amount": 50}
    out = route.resolve("purchase.queue", args, "")
    assert out["action"] == "QUEUE_PURCHASE"
    assert out["target"] == "amazon"
    assert json.loads(out["payload"]) == args
    # Empty reason → synthesized justification.
    assert "purchase.queue" in out["justification"]


def test_tool_envelope_route_wraps_tool_and_args() -> None:
    route = ApprovalRoute(
        action=ApprovalAction.EXECUTE_DESTRUCTIVE,
        target_arg="key",
        payload_kind=ApprovalPayloadKind.TOOL_ENVELOPE,
    )
    out = route.resolve("memory.delete", {"key": "note-1"}, "")
    assert out["action"] == "EXECUTE_DESTRUCTIVE"
    assert out["target"] == "note-1"
    assert json.loads(out["payload"]) == {
        "tool": "memory.delete",
        "args": {"key": "note-1"},
    }


def test_body_arg_route_without_payload_arg_raises() -> None:
    route = ApprovalRoute(
        action=ApprovalAction.SEND_EMAIL,
        target_arg="to",
        payload_kind=ApprovalPayloadKind.BODY_ARG,
        payload_arg=None,
    )
    with pytest.raises(ValueError, match="BODY_ARG route needs payload_arg"):
        route.resolve("email.send", {"to": "a"}, "")


# ---- end-to-end through LabeledToolClient -------------------------------


@pytest.fixture
async def app(tmp_path: Path) -> App:
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await a.startup()
    return a


async def test_require_approval_outcome_carries_resolved_submission(
    app: App,
) -> None:
    """purchase.queue with financial label fires financial-meets-purchase
    (REQUIRE_APPROVAL). The outcome must carry a ready-to-submit
    approval_submission resolved from the tool's declared route."""
    s = await app.graph.new()
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=10_000,
    )
    from capabledeputy.policy.labels import AxisA

    financial_axis_a = AxisA(
        categories=(
            CategoryTag("financial", Tier.REGULATED, assignment_provenance="source-declared"),
        )
    )
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({cap}),
        axis_a=financial_axis_a,
    )
    client = LabeledToolClient(app.registry, app.graph, app.audit)

    outcome = await client.call_tool(
        s.id,
        "purchase.queue",
        {"vendor": "amazon", "item": "towels", "amount": 50},
    )
    assert outcome.decision.value == "require_approval"
    assert outcome.approval_submission is not None
    sub = outcome.approval_submission
    assert sub["action"] == "QUEUE_PURCHASE"
    assert sub["target"] == "amazon"
    assert json.loads(sub["payload"])["item"] == "towels"


async def test_destructive_outcome_carries_tool_envelope_submission(
    app: App,
) -> None:

    app.memory.write("k", "v", LabelState())
    s = await app.graph.new()
    cap = Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        allows_destructive=False,
    )
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    client = LabeledToolClient(app.registry, app.graph, app.audit)

    outcome = await client.call_tool(
        s.id,
        "memory.delete",
        {"key": "k"},
    )
    assert outcome.decision.value == "require_approval"
    sub = outcome.approval_submission
    assert sub is not None
    assert sub["action"] == "EXECUTE_DESTRUCTIVE"
    assert sub["target"] == "k"
    assert json.loads(sub["payload"]) == {
        "tool": "memory.delete",
        "args": {"key": "k"},
    }


async def test_allow_outcome_has_no_approval_submission(app: App) -> None:

    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    app.memory.write("k", "v", LabelState())
    client = LabeledToolClient(app.registry, app.graph, app.audit)

    outcome = await client.call_tool(s.id, "memory.read", {"key": "k"})
    assert outcome.decision.value == "allow"
    assert outcome.approval_submission is None


async def test_every_destructive_native_tool_declares_a_route(app: App) -> None:
    """Regression guard: the four destructive tools + the two egress
    tools must declare an approval_route, else auto-submit silently
    falls back to manual /submit."""
    expected = {
        "email.send",
        "purchase.queue",
        "memory.update",
        "memory.delete",
        "calendar.update_event",
        "calendar.delete_event",
    }
    for name in expected:
        tool = app.registry.get(name)
        assert tool.approval_route is not None, f"{name} missing approval_route"
