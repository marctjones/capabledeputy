"""Calendar tool stub (DESIGN.md §7.4 — `confidential.personal` source).

Demo-grade stub backed by an in-memory store. Production deployments
should wrap a real calendar MCP server (Google Calendar, CalDAV) via
`upstream/`; that path applies the same labels via YAML config. This
native stub exists so the demos in `docs/demos/` run deterministically
without a real upstream dependency.

Two tools:

  - `calendar.events_today` — read-only listing scoped to a date,
    labeled `confidential.personal`.
  - `calendar.create_event` — write a new event; the writer's session
    label set propagates onto the stored event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


@dataclass(frozen=True)
class CalendarEvent:
    id: UUID
    title: str
    starts_at: datetime
    ends_at: datetime
    notes: str = ""
    labels: frozenset[Label] = field(default_factory=frozenset)


class CalendarStore:
    def __init__(self) -> None:
        self._events: dict[UUID, CalendarEvent] = {}

    def add(self, event: CalendarEvent) -> None:
        self._events[event.id] = event

    def all(self) -> list[CalendarEvent]:
        return list(self._events.values())

    def for_day(self, day: datetime) -> list[CalendarEvent]:
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        end = start + timedelta(days=1)
        return [e for e in self._events.values() if start <= e.starts_at < end]


def make_calendar_tools(store: CalendarStore) -> list[ToolDefinition]:
    async def events_today(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        day_str = str(args.get("date", ""))
        if day_str:
            day = datetime.fromisoformat(day_str)
            if day.tzinfo is None:
                day = day.replace(tzinfo=UTC)
        else:
            day = datetime.now(UTC)
        events = store.for_day(day)
        return ToolResult(
            output={
                "date": day.date().isoformat(),
                "events": [
                    {
                        "id": str(e.id),
                        "title": e.title,
                        "starts_at": e.starts_at.isoformat(),
                        "ends_at": e.ends_at.isoformat(),
                        "notes": e.notes,
                    }
                    for e in events
                ],
            },
        )

    async def create_event(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        title = str(args["title"])
        starts = datetime.fromisoformat(str(args["starts_at"]))
        ends = datetime.fromisoformat(str(args["ends_at"]))
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=UTC)
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=UTC)
        event = CalendarEvent(
            id=uuid4(),
            title=title,
            starts_at=starts,
            ends_at=ends,
            notes=str(args.get("notes", "")),
            labels=ctx.label_set,
        )
        store.add(event)
        return ToolResult(output={"created": True, "id": str(event.id)})

    return [
        ToolDefinition(
            name="calendar.events_today",
            description=(
                "List calendar events for a specific day (default today). "
                "Returns confidential.personal-labeled data. Required args: "
                "date (ISO date string, optional)."
            ),
            capability_kind=CapabilityKind.CALENDAR_READ,
            handler=events_today,
            target_arg="date",
            inherent_labels=frozenset({Label.CONFIDENTIAL_PERSONAL}),
            parameters_schema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD). Default: today.",
                    },
                },
            },
        ),
        ToolDefinition(
            name="calendar.create_event",
            description=(
                "Create a new calendar event. The session's label set "
                "propagates onto the stored event. Required args: title, "
                "starts_at (ISO datetime), ends_at (ISO datetime)."
            ),
            capability_kind=CapabilityKind.CALENDAR_WRITE,
            handler=create_event,
            target_arg="title",
            parameters_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "starts_at": {"type": "string"},
                    "ends_at": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["title", "starts_at", "ends_at"],
            },
        ),
    ]
