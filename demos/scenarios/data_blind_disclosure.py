"""Data-blind disclosure — Pattern (3) ReferenceHandle binding.

The planner manipulates an opaque UUID. The dispatcher binds the real
value AT THE LAST POSSIBLE MOMENT — AFTER decide() approves — and
emits a pattern3.handle_bind audit event recording the canonical
destination. Cross-session forgery is structurally refused.
"""

from __future__ import annotations

from dataclasses import replace as _dc_replace
from typing import Any

import pytest

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.patterns.reference_handle import (
    ReferenceHandleError,
    ReferenceHandleStore,
    ResolvedLabels,
)
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient, PolicyContext
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from demos.scenarios._helpers import (
    ai,
    audit,
    demo_header,
    note,
    policy,
    policy_outcome,
    step,
    tool,
    user,
)


async def _api_post_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    return ToolResult(output={"posted": True, "body_received": args.get("body")})


def _handle_aware_tool() -> ToolDefinition:
    return ToolDefinition(
        name="api.post",
        description="post body to an external service",
        capability_kind=CapabilityKind.WEB_FETCH,
        handler=_api_post_handler,
        target_arg="url",
        accepts_handles=True,
        handle_arg_names=("body",),
        effect_class="data.api_post",
        surfaces_destination_id=True,
    )


@pytest.mark.asyncio
async def test_data_blind_disclosure_demo(tmp_path: Any) -> None:
    demo_header(
        "Data-Blind Disclosure — Pattern ③ ReferenceHandle",
        blurb=(
            "Planner sees a UUID. Dispatcher binds the real value after "
            "decide() approves. Audit logs the canonical destination. "
            "Cross-session forgery refused structurally."
        ),
        models=(
            "FR-047 unforgeable per-session handles",
            "FR-043 / FR-048 canonical destination id",
        ),
        patterns=("Pattern ③ ReferenceHandle binding",),
    )

    writer = AuditWriter(tmp_path / "audit.jsonl")
    registry = ToolRegistry()
    registry.register(_handle_aware_tool())
    graph = SessionGraph()
    store = ReferenceHandleStore()
    ctx = PolicyContext(handle_store=store)
    client = LabeledToolClient(registry, graph, writer, policy_context=ctx)

    s = await graph.new()
    s = _dc_replace(
        s,
        capability_set=frozenset(
            {
                Capability(
                    kind=CapabilityKind.WEB_FETCH,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )
    graph._sessions[s.id] = s

    step(1, "Runtime issues a handle for the sensitive payload")
    handle = store.issue(
        s.id,
        value="SECRET_API_TOKEN_xyz123",
        labels=ResolvedLabels(axis_a=("auth",), axis_b=("principal-direct",)),
    )
    note(f"handle id = {handle.id} — planner sees THIS, never the value.")

    step(2, "Planner calls api.post passing the handle UUID in `body`")
    ai('call api.post(url="https://example.com/ingest", body=<handle-uuid>)')
    outcome = await client.call_tool(
        s.id,
        "api.post",
        {"url": "https://example.com/ingest", "body": str(handle.id)},
    )
    assert outcome.decision is Decision.ALLOW
    policy_outcome(outcome)
    body_seen = outcome.output["body_received"]
    tool(f"handler received body = {body_seen!r}")
    note("The real value reached the destination; the planner never held it.")
    assert body_seen == "SECRET_API_TOKEN_xyz123"

    step(3, "Audit trail records the canonical destination")
    events = await writer.read_all()
    binds = [e for e in events if e.event_type is EventType.PATTERN3_HANDLE_BIND]
    assert len(binds) == 1
    dest = binds[0].payload["destination_canonical_id"]
    audit(f"pattern3.handle_bind  ·  destination = {dest}")
    assert dest == "https://example.com/ingest"

    step(4, "Cross-session forgery — refused structurally")
    user("attempt: redeem a session-B handle in session A")
    s_b = await graph.new()
    handle_b = store.issue(
        s_b.id,
        value="OTHER_SESSION_VALUE",
        labels=ResolvedLabels(axis_a=("auth",), axis_b=("principal-direct",)),
    )
    try:
        store.bind(
            session_id=s.id,
            handle_id=handle_b.id,
            destination_canonical_id="https://example.com/ingest",
            tool="api.post",
            audit_id=handle_b.id,
        )
        raise AssertionError("expected ReferenceHandleError")
    except ReferenceHandleError as e:
        policy("refused", rule="cross-session", rationale=str(e))
