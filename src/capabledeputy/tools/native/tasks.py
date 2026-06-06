"""Personal task / reminder stub tool.

Closes the only priority-workflow gap: a lightweight to-do list so the
"task & reminder management" workflow (Notion/Things/Trello-class) can
be exercised end-to-end deterministically. Real deployments wrap a
Google Tasks / Todoist MCP server via `upstream/` so the same labels and
gates apply.

Tasks are personal user state, so list contents carry
`confidential.personal`. Capability mapping is granular on purpose:

  - `tasks.add`      -> CREATE_FS  (non-destructive: a new item)
  - `tasks.list`     -> READ_FS
  - `tasks.complete` -> MODIFY_FS  (mutates existing state, so the
                         destructive-op gate engages; the intended
                         pattern is a pre-granted allows_destructive
                         capability scoped to the task list, NOT a
                         per-action human approval — low-stakes,
                         deliberate, unattended).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import (
    CategoryTag,
    Label,
    LabelState,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

_PERSONAL = frozenset({Label.CONFIDENTIAL_PERSONAL})
_PERSONAL_TAGS = LabelState(
    a=frozenset(
        {
            CategoryTag(
                category="personal", tier=Tier.REGULATED, assignment_provenance="source-declared"
            )
        }
    )
)


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    notes: str
    done: bool
    created_at: datetime


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def add(self, title: str, notes: str = "") -> Task:
        task = Task(
            id=uuid4().hex[:8],
            title=title,
            notes=notes,
            done=False,
            created_at=datetime.now(UTC),
        )
        self._tasks[task.id] = task
        return task

    def all(self) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at)

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def complete(self, task_id: str) -> bool:
        existing = self._tasks.get(task_id)
        if existing is None:
            return False
        self._tasks[task_id] = replace(existing, done=True)
        return True

    def edit(
        self,
        task_id: str,
        *,
        title: str | None = None,
        notes: str | None = None,
    ) -> bool:
        existing = self._tasks.get(task_id)
        if existing is None:
            return False
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if notes is not None:
            updates["notes"] = notes
        if not updates:
            return True
        self._tasks[task_id] = replace(existing, **updates)
        return True

    def remove(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None


def make_tasks_tools(store: TaskStore) -> list[ToolDefinition]:
    async def tasks_add(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        task = store.add(
            title=str(args["title"]),
            notes=str(args.get("notes", "")),
        )
        return ToolResult(
            output={"id": task.id, "title": task.title},
            additional_labels=_PERSONAL,
        )

    async def tasks_list(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        include_done = bool(args.get("include_done", False))
        items = [t for t in store.all() if include_done or not t.done]
        return ToolResult(
            output={
                "tasks": [{"id": t.id, "title": t.title, "done": t.done} for t in items],
            },
            additional_labels=_PERSONAL,
        )

    async def tasks_complete(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        ok = store.complete(str(args["id"]))
        return ToolResult(output={"completed": ok}, additional_labels=_PERSONAL)

    async def tasks_edit(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        ok = store.edit(
            str(args["id"]),
            title=args.get("title"),
            notes=args.get("notes"),
        )
        return ToolResult(output={"edited": ok}, additional_labels=_PERSONAL)

    async def tasks_delete(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        ok = store.remove(str(args["id"]))
        return ToolResult(output={"deleted": ok}, additional_labels=_PERSONAL)

    return [
        ToolDefinition(
            name="tasks.add",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="tasks.add"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            effect_class="data.create_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "Add a personal to-do item. Non-destructive (CREATE_FS). "
                "Required args: title (string); optional notes (string)."
            ),
            capability_kind=CapabilityKind.CREATE_FS,
            handler=tasks_add,
            target_arg="title",
            inherent_labels=_PERSONAL,
            inherent_tags=_PERSONAL_TAGS,
            parameters_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["title"],
            },
        ),
        ToolDefinition(
            name="tasks.list",
            operations=(Operation(EffectClass.FETCH, subtype="tasks.list"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "List personal to-do items (open by default). Read-only. "
                "Optional arg: include_done (bool)."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=tasks_list,
            inherent_labels=_PERSONAL,
            inherent_tags=_PERSONAL_TAGS,
            parameters_schema={
                "type": "object",
                "properties": {"include_done": {"type": "boolean"}},
            },
        ),
        ToolDefinition(
            name="tasks.complete",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="tasks.complete"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            effect_class="data.modify_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "Mark a to-do item done. Mutates existing state "
                "(MODIFY_FS): the destructive-op gate engages unless the "
                "capability is granted allows_destructive. Required args: "
                "id (string)."
            ),
            capability_kind=CapabilityKind.MODIFY_FS,
            handler=tasks_complete,
            target_arg="id",
            inherent_labels=_PERSONAL,
            inherent_tags=_PERSONAL_TAGS,
            parameters_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        ),
        ToolDefinition(
            name="tasks.edit",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="tasks.edit"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            effect_class="data.modify_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "Edit an existing task's title or notes. MODIFY_FS — "
                "destructive-op gate fires unless the cap has "
                "allows_destructive. Required args: id (string); "
                "optional title, notes."
            ),
            capability_kind=CapabilityKind.MODIFY_FS,
            handler=tasks_edit,
            target_arg="id",
            inherent_labels=_PERSONAL,
            inherent_tags=_PERSONAL_TAGS,
            parameters_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["id"],
            },
        ),
        ToolDefinition(
            name="tasks.delete",
            operations=(Operation(EffectClass.DESTROY, subtype="tasks.delete"),),
            risk_ids=("RISK-DESTRUCTIVE-WRITE",),
            surfaces_destination_id=True,
            effect_class="data.delete_local",
            default_reversibility={"degree": "irreversible", "agent": "external"},
            tool_provenance="operator-curated",
            description=(
                "Delete a task by id. DELETE_FS + irreversible/external "
                "— the reversibility gate refuses without override. "
                "Required args: id (string)."
            ),
            capability_kind=CapabilityKind.DELETE_FS,
            handler=tasks_delete,
            target_arg="id",
            inherent_labels=_PERSONAL,
            inherent_tags=_PERSONAL_TAGS,
            parameters_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        ),
    ]
