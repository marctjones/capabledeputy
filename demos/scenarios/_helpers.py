"""Shared helpers for the demo scenarios.

Every demo constructs its own `App` with a tailored `PolicyContext`.
This module centralizes the boilerplate so each scenario can focus on
the security promise it's demonstrating.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from capabledeputy.app import App
from capabledeputy.audit.events import EventType
from capabledeputy.policy.labels import (
    AxisA,
    AxisACategory,
    AxisB,
    AxisBEntry,
    AxisD,
    ProvenanceLevel,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import Session
from capabledeputy.tools.client import PolicyContext

# Frozen clock used across all demos. Determinism is part of the
# security promise (SC-002); demos hardcode the same `now` so the
# audit trails are byte-identical across runs.
FROZEN_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def make_app(
    tmp_path: Path,
    *,
    policy_context: PolicyContext | None = None,
    purposes: Any = None,
) -> App:
    """Construct a demo App with audit log + state DB rooted in tmp_path.
    PolicyContext is the tailored bus the demo provides; None falls
    back to legacy v0.7 behavior (used by the few demos that explicitly
    contrast v0.7 vs v2)."""
    return App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        policy_context=policy_context,
        purposes=purposes,
        enable_policy_preview=False,
    )


async def make_session(
    app: App,
    *,
    axis_a_categories: tuple[tuple[str, Tier], ...] = (),
    provenance: ProvenanceLevel = ProvenanceLevel.PRINCIPAL_DIRECT,
    initiator: str = "principal:alice",
    authentication: str = "device-bound",
    clearance_profile_id: str | None = None,
    purpose_handle: str = "unset",
    capabilities: frozenset[Any] = frozenset(),
) -> Session:
    """Bootstrap a session with the demo's required axes + capabilities.
    Async because we're called from inside pytest-asyncio's loop."""
    from dataclasses import replace as dc_replace

    s = await app.graph.new(purpose_handle=purpose_handle)
    axis_a = AxisA(
        categories=tuple(AxisACategory(category=cat, tier=tier) for cat, tier in axis_a_categories),
    )
    axis_b = AxisB(entries=(AxisBEntry(level=provenance),))
    axis_d = AxisD(initiator=initiator, authentication=authentication)
    updated = dc_replace(
        s,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        capability_set=capabilities,
        clearance_profile_id=clearance_profile_id,
    )
    app.graph._sessions[s.id] = updated
    await app.graph._save(updated)
    return updated


def narrate(title: str, body: str) -> None:
    """Operator-facing narrative line. Visible when pytest is run with
    -s; silent under normal CI."""
    print(f"\n=== {title} ===")
    for line in body.strip().split("\n"):
        print(f"  {line}")


async def collect_events(app: App) -> list[Any]:
    """Read every audit event the demo emitted so far."""
    return await app.audit.read_all()


def event_types(events: list[Any]) -> list[EventType]:
    return [e.event_type for e in events]


__all__ = [
    "FROZEN_NOW",
    "collect_events",
    "event_types",
    "make_app",
    "make_session",
    "narrate",
]
