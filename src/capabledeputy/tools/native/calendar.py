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

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.route import ApprovalPayloadKind, ApprovalRoute
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import (
    CategoryTag,
    Label,
    LabelState,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

_CAL_DESTRUCTIVE_ROUTE = ApprovalRoute(
    action=ApprovalAction.EXECUTE_DESTRUCTIVE,
    target_arg="id",
    payload_kind=ApprovalPayloadKind.TOOL_ENVELOPE,
)


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

    def get(self, event_id: UUID) -> CalendarEvent | None:
        return self._events.get(event_id)

    def update(self, event: CalendarEvent) -> bool:
        if event.id not in self._events:
            return False
        self._events[event.id] = event
        return True

    def remove(self, event_id: UUID) -> bool:
        return self._events.pop(event_id, None) is not None

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

    async def update_event(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Modify an existing event. Tagged MODIFY_CAL — the
        destructive-op gate fires unless the capability has
        allows_destructive=True or the user approves."""
        event_id = UUID(str(args["id"]))
        existing = store.get(event_id)
        if existing is None:
            return ToolResult(output={"ok": False, "error": "event not found"})
        title = str(args.get("title", existing.title))
        notes = str(args.get("notes", existing.notes))
        starts = (
            datetime.fromisoformat(str(args["starts_at"]))
            if "starts_at" in args
            else existing.starts_at
        )
        ends = (
            datetime.fromisoformat(str(args["ends_at"])) if "ends_at" in args else existing.ends_at
        )
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=UTC)
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=UTC)
        new = CalendarEvent(
            id=event_id,
            title=title,
            starts_at=starts,
            ends_at=ends,
            notes=notes,
            labels=existing.labels | ctx.label_set,
        )
        store.update(new)
        return ToolResult(output={"ok": True, "id": str(event_id), "modified": True})

    async def delete_event(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        """Remove an event. Tagged DELETE_CAL — destructive-op gate
        fires by default."""
        event_id = UUID(str(args["id"]))
        if not store.remove(event_id):
            return ToolResult(output={"ok": False, "error": "event not found"})
        return ToolResult(output={"ok": True, "id": str(event_id), "deleted": True})

    return [
        ToolDefinition(
            name="calendar.events_today",
            effect_class="data.read_calendar",
            operations=(Operation(EffectClass.FETCH, subtype="calendar.read"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "List calendar events for a specific day (default today). "
                "Returns confidential.personal-labeled data. Required args: "
                "date (ISO date string, optional)."
            ),
            capability_kind=CapabilityKind.CALENDAR_READ,
            handler=events_today,
            target_arg="date",
            inherent_labels=frozenset({Label.CONFIDENTIAL_PERSONAL}),
            inherent_tags=LabelState(
                a=frozenset(
                    {
                        CategoryTag(
                            category="personal",
                            tier=Tier.REGULATED,
                            assignment_provenance="source-declared",
                        )
                    }
                )
            ),
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
            effect_class="data.write_calendar",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="calendar.create"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            default_reversibility={"degree": "reversible-with-friction", "agent": "human"},
            tool_provenance="operator-curated",
            description=(
                "Create a new calendar event. Non-destructive (bypasses "
                "destructive-op gate). The session's label set propagates "
                "onto the stored event. Required args: title, starts_at, "
                "ends_at."
            ),
            capability_kind=CapabilityKind.CREATE_CAL,
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
        ToolDefinition(
            name="calendar.update_event",
            effect_class="data.modify_calendar",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="calendar.modify"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            default_reversibility={"degree": "reversible-with-friction", "agent": "human"},
            tool_provenance="operator-curated",
            description=(
                "Update fields on an existing event. Destructive: gated by "
                "the destructive-op rule. Required args: id (event uuid). "
                "Optional: title, starts_at, ends_at, notes."
            ),
            capability_kind=CapabilityKind.MODIFY_CAL,
            handler=update_event,
            target_arg="id",
            approval_route=_CAL_DESTRUCTIVE_ROUTE,
            parameters_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "starts_at": {"type": "string"},
                    "ends_at": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["id"],
            },
        ),
        ToolDefinition(
            name="calendar.delete_event",
            effect_class="data.delete_calendar",
            operations=(Operation(EffectClass.DESTROY, subtype="calendar.delete"),),
            risk_ids=("RISK-DESTRUCTIVE-WRITE",),
            surfaces_destination_id=True,
            default_reversibility={"degree": "irreversible", "agent": "external"},
            tool_provenance="operator-curated",
            description=(
                "Remove an event by id. Destructive: gated by the "
                "destructive-op rule. Required args: id (event uuid)."
            ),
            capability_kind=CapabilityKind.DELETE_CAL,
            handler=delete_event,
            target_arg="id",
            approval_route=_CAL_DESTRUCTIVE_ROUTE,
            parameters_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        ),
    ]
