"""Assurance slice #3 — Pattern ③ reference-handle redirection-resistance.

The data_blind_disclosure demo proves the happy path + a store-level cross-
session refusal. This is the ADVERSARIAL half the assurance plan flagged as
missing: an injection that fully controls the planner's tool arguments still
cannot exfiltrate or redirect a handle-bound value. Four claims, end-to-end
through the real source-flow binder used by the dispatcher:

  A. Data-blind — the planner only ever holds an opaque UUID, never the value.
  B. Forged handle — a guessed/fabricated id binds nothing; only the opaque
     token flows downstream (no value is fabricated).
  C. Cross-session theft — a handle stolen from another session is refused at
     the dispatcher; the thief's tool call carries the token, not the value.
  D. No redirect — handle→value is frozen at issue; there is no planner-
     reachable path to repoint handle X at a different value, and the bind
     trail records exactly where the frozen value landed.

If any of these failed, prompt-injected content could turn the reference-handle
mechanism into an exfiltration primitive — so each is a real regression catch.
"""

from __future__ import annotations

from dataclasses import replace as _dc_replace
from typing import Any
from uuid import uuid4

import pytest

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.patterns.reference_handle import (
    ReferenceHandleStore,
    ResolvedLabels,
    is_planner_safe_token,
)
from capabledeputy.policy.bindings import BindingSet
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)

_SECRET = "SECRET_API_TOKEN_xyz123"


async def _api_post_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    # The handler echoes what it ACTUALLY received in `body` — that is the
    # observable that tells us whether a real value or an opaque token landed.
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
        operations=(Operation(EffectClass.COMMUNICATE, subtype="api.post"),),
        risk_ids=("RISK-DATA-EXFIL-AGENT-TOOLS",),
        parameters_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["url", "body"],
        },
        surfaces_destination_id=True,
    )


async def _make_client(tmp_path: Any):
    writer = AuditWriter(tmp_path / "audit.jsonl")
    registry = ToolRegistry()
    registry.register(_handle_aware_tool())
    graph = SessionGraph()
    store = ReferenceHandleStore()
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(handle_store=store, bindings=_example_allowlist()),
    )
    return client, graph, store, writer


async def _session_with_web(graph: SessionGraph):
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
    return s


async def _post(client, sid, body: str):
    return await client.call_tool(
        sid,
        "api.post",
        {"url": "https://example.com/ingest", "body": body},
    )


# --- A. data-blind --------------------------------------------------


@pytest.mark.asyncio
async def test_planner_only_ever_holds_an_opaque_token(tmp_path: Any) -> None:
    _client, graph, store, _w = await _make_client(tmp_path)
    s = await _session_with_web(graph)
    handle = store.issue(
        s.id,
        value=_SECRET,
        labels=ResolvedLabels(axis_a=("auth",), axis_b=("principal-direct",)),
    )
    # The planner-visible token is a UUID, not the value (no value field on
    # the handle wire format).
    assert is_planner_safe_token(str(handle.id))
    assert _SECRET not in str(handle.id)


# --- B. forged handle fabricates nothing ----------------------------


@pytest.mark.asyncio
async def test_forged_handle_binds_no_value(tmp_path: Any) -> None:
    """An injection that fabricates a plausible handle id gets NOTHING — the
    bind refuses the unknown id, so the opaque token flows downstream, never
    a fabricated value, and no handle_bind audit event is emitted."""
    client, graph, _store, writer = await _make_client(tmp_path)
    s = await _session_with_web(graph)
    forged = str(uuid4())  # never issued by the store
    outcome = await _post(client, s.id, forged)
    assert outcome.decision is Decision.ALLOW  # the call itself is allowed...
    # ...but the body the handler saw is the raw forged TOKEN, not a value.
    assert outcome.output["body_received"] == forged
    events = await writer.read_all()
    assert not [e for e in events if e.event_type is EventType.PATTERN3_HANDLE_BIND]


# --- C. cross-session theft refused end-to-end ----------------------


@pytest.mark.asyncio
async def test_stolen_cross_session_handle_discloses_nothing(tmp_path: Any) -> None:
    """The attacker learns a handle id issued in a victim session and replays
    it inside their OWN session's tool call. The dispatcher's bind refuses the
    cross-session id, so the attacker's call carries the opaque token — the
    victim's value is never disclosed."""
    client, graph, store, writer = await _make_client(tmp_path)
    victim = await _session_with_web(graph)
    attacker = await _session_with_web(graph)
    victim_handle = store.issue(
        victim.id,
        value="VICTIM_SECRET",
        labels=ResolvedLabels(axis_a=("auth",), axis_b=("principal-direct",)),
    )
    outcome = await _post(client, attacker.id, str(victim_handle.id))
    # Refused substitution ⇒ token flows, value does NOT.
    assert outcome.output["body_received"] == str(victim_handle.id)
    assert outcome.output["body_received"] != "VICTIM_SECRET"
    events = await writer.read_all()
    assert not [e for e in events if e.event_type is EventType.PATTERN3_HANDLE_BIND]


# --- D. frozen binding / no redirect --------------------------------


@pytest.mark.asyncio
async def test_handle_value_is_frozen_and_bind_trail_records_destination(
    tmp_path: Any,
) -> None:
    """The legitimate bind resolves to the FROZEN value and records exactly
    one destination. There is no planner-reachable API to repoint the handle
    at a different value — so an injection cannot redirect handle X's value to
    a new place; it can only (B) forge or (C) steal, both of which disclose
    nothing."""
    client, graph, store, writer = await _make_client(tmp_path)
    s = await _session_with_web(graph)
    handle = store.issue(
        s.id,
        value=_SECRET,
        labels=ResolvedLabels(axis_a=("auth",), axis_b=("principal-direct",)),
    )
    outcome = await _post(client, s.id, str(handle.id))
    assert outcome.decision is Decision.ALLOW
    assert outcome.output["body_received"] == _SECRET  # frozen value resolved

    trail = store.bind_trail(handle.id)
    assert len(trail) == 1
    assert trail[0].destination_canonical_id == "https://example.com/ingest"
    # The dispatcher emitted exactly one bind event for the real destination.
    events = await writer.read_all()
    binds = [e for e in events if e.event_type is EventType.PATTERN3_HANDLE_BIND]
    assert len(binds) == 1
    assert binds[0].payload["destination_canonical_id"] == "https://example.com/ingest"


def _example_allowlist() -> BindingSet:
    """Allowlist example.com so a Pattern-3 handle-routed web.fetch is SAFE
    ROUTING (operator-declared destination), not an ungated exfil. The
    destination-aware fetch floor (#293/#296) gates confidential fetches to
    non-allowlisted hosts; forged/stolen-handle resistance is unchanged."""
    from capabledeputy.policy.bindings import BindingSet, SourceLocationLabelBinding
    from capabledeputy.policy.tiers import Tier

    return BindingSet(
        bindings=(
            SourceLocationLabelBinding(
                name="ExampleIngest",
                scope_pattern_canonical="https://example.com/*",
                category="proprietary_work",
                default_tier=Tier.SENSITIVE,
            ),
        ),
    )
