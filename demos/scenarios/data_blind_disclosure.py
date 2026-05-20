"""Data-blind disclosure — Pattern (3) ReferenceHandle binding.

Story:
  A planner needs to send a sensitive payload to an external service
  but should never see the raw value. CapableDeputy wraps the value
  in an unforgeable, per-session `ReferenceHandle`. The planner
  manipulates the handle as opaque text; the dispatcher binds the
  real value AT THE LAST POSSIBLE MOMENT — AFTER decide() approves —
  and emits a `pattern3.handle_bind` audit event that records the
  canonical destination id.

  Two structural invariants are exercised:

    1. The planner-visible args contain the UUID, never the value.
    2. Cross-session forgery is refused: a handle issued in session
       A cannot be redeemed in session B.

Security models exercised:
  - Pattern (3) ReferenceHandle (data-blind planning)
  - FR-043 / FR-048 canonical destination ids (audit records where
    a value landed, not just that the tool ran)
"""

from __future__ import annotations

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
from demos.scenarios._helpers import narrate


async def _api_post_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """A tool that posts a body to an external service. We record what
    the handler ACTUALLY saw — the demo asserts it's the real value,
    not the handle UUID."""
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
    narrate(
        "Data-Blind Disclosure — Pattern (3) ReferenceHandle",
        """
        Planner sees a handle UUID. Dispatcher binds the real value
        AFTER decide(). Cross-session forgery refused. Audit log
        records the canonical destination.
        """,
    )

    writer = AuditWriter(tmp_path / "audit.jsonl")
    registry = ToolRegistry()
    registry.register(_handle_aware_tool())
    graph = SessionGraph()
    store = ReferenceHandleStore()
    ctx = PolicyContext(handle_store=store)
    client = LabeledToolClient(registry, graph, writer, policy_context=ctx)

    s = await graph.new()
    from dataclasses import replace as _dc_replace

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

    narrate("Step 1", "Runtime issues a handle for the sensitive payload.")
    handle = store.issue(
        s.id,
        value="SECRET_API_TOKEN_xyz123",
        labels=ResolvedLabels(axis_a=("auth",), axis_b=("principal-direct",)),
    )
    narrate("  → handle", f"id={handle.id} (planner sees this — NOT the value)")

    narrate(
        "Step 2",
        "Planner calls api.post passing the handle UUID in `body`.\n"
        "    Dispatcher gates the call, then BINDS the real value.",
    )
    outcome = await client.call_tool(
        s.id,
        "api.post",
        {"url": "https://example.com/ingest", "body": str(handle.id)},
    )
    assert outcome.decision is Decision.ALLOW
    body_seen = outcome.output["body_received"]
    narrate(
        "  → handler view",
        f"handler received body = {body_seen!r}\n"
        "    The real value reached the destination; the planner never\n"
        "    held it.",
    )
    assert body_seen == "SECRET_API_TOKEN_xyz123"

    narrate("Step 3", "Verify the audit trail recorded the canonical destination.")
    events = await writer.read_all()
    binds = [e for e in events if e.event_type is EventType.PATTERN3_HANDLE_BIND]
    assert len(binds) == 1
    narrate(
        "  → bind event",
        f"payload = {binds[0].payload}",
    )
    assert binds[0].payload["destination_canonical_id"] == "https://example.com/ingest"

    # Cross-session forgery — issue a handle in session B; try to use
    # it in session A. The store refuses.
    narrate("Step 4", "Cross-session forgery refused (structural).")
    s_b = await graph.new()
    handle_b = store.issue(
        s_b.id,
        value="OTHER_SESSION_VALUE",
        labels=ResolvedLabels(axis_a=("auth",), axis_b=("principal-direct",)),
    )
    try:
        store.bind(
            session_id=s.id,  # WRONG session
            handle_id=handle_b.id,
            destination_canonical_id="https://example.com/ingest",
            tool="api.post",
            audit_id=handle_b.id,  # reused for the demo
        )
        raise AssertionError("expected ReferenceHandleError")
    except ReferenceHandleError as e:
        narrate("  → refusal", f"{type(e).__name__}: {e}")
