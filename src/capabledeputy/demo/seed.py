"""Apply a Scenario's seed data to the running App's stores.

Called from the `demo.seed` JSON-RPC handler. The seeding is additive
— each call appends to whatever the daemon already has in memory; the
caller is responsible for picking a fresh scenario name if isolation
between scenarios matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from capabledeputy.app import App
from capabledeputy.demo.scenarios import (
    Scenario,
    absolute_time,
    utcnow_floor_minute,
)
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityOrigin,
)
from capabledeputy.tools.native.calendar import CalendarEvent
from capabledeputy.tools.native.inbox import InboundMessage


@dataclass(frozen=True)
class SeedResult:
    session_id: UUID
    inbox_count: int
    calendar_count: int
    memory_count: int
    capabilities_granted: int


async def apply_scenario(app: App, scenario: Scenario) -> SeedResult:
    """Create a session for the scenario, populate the stores, and
    grant the scenario's capabilities. Returns the new session id plus
    counts for the rendered summary in the REPL."""
    session = await app.graph.new(intent=scenario.intent)

    for cap_spec in scenario.capabilities:
        cap = Capability(
            kind=cap_spec.kind,
            pattern=cap_spec.pattern,
            expiry=CapabilityExpiry.SESSION,
            origin=CapabilityOrigin.SYSTEM_DEFAULT,
            max_amount=cap_spec.max_amount,
            allows_destructive=cap_spec.allows_destructive,
        )
        await app.graph.grant_capability(session.id, cap)

    reference = utcnow_floor_minute()

    for msg in scenario.inbox:
        app.inbox.add(
            InboundMessage(
                id=msg.id,
                sender=msg.sender,
                subject=msg.subject,
                body=msg.body,
                received_at=reference - timedelta(minutes=msg.minutes_ago),
            ),
        )

    for event in scenario.calendar:
        starts_at = absolute_time(reference, event.starts_in_minutes)
        ends_at = starts_at + timedelta(minutes=event.duration_minutes)
        app.calendar.add(
            CalendarEvent(
                id=uuid4(),
                title=event.title,
                starts_at=starts_at,
                ends_at=ends_at,
                notes=event.notes,
                labels=event.labels,
            ),
        )

    for entry in scenario.memory:
        app.memory.write(entry.key, entry.value, entry.labels)

    return SeedResult(
        session_id=session.id,
        inbox_count=len(scenario.inbox),
        calendar_count=len(scenario.calendar),
        memory_count=len(scenario.memory),
        capabilities_granted=len(scenario.capabilities),
    )
