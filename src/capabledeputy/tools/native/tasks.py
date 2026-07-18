"""Personal task / reminder tool — a real, LOCAL, persistent to-do list.

#325 — de-stubbed from an in-memory dict to a SQLite-backed store so a reminder
survives a daemon restart (the "create a real reminder" acceptance, met locally
with NO external credentials). Per spike #312 tasks stays NATIVE: no production
Google Tasks / Todoist MCP server exists to wire, and the workflow is entirely
local user state. A future upstream provider can still be wired behind the same
labels and gates.

Storage joins the shared state DB additively (`CREATE TABLE IF NOT EXISTS`, same
convention as onguard / memory / admission), so it inherits that file's #321
non-destructive lifecycle without owning a schema-version of its own. Connection
is lazy: the first task operation happens mid-turn, long after the session
store's startup lifecycle has settled the file.

Tasks are personal user state, so list contents carry `confidential.personal`.
Capability mapping is granular on purpose:

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

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

# Additive: no schema_version row — the session store (#321) owns the shared
# state.db's version; tasks is a peer table like onguard/memory/admission.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

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


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        notes=row["notes"],
        done=bool(row["done"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class TaskStore:
    """SQLite-backed personal task list. `db_path` defaults to an in-memory DB
    (ephemeral — the prior stub's behavior, kept for tests + no-arg callers);
    the daemon passes the shared state.db path for persistence across restarts."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._con: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        # Lazy connect + additive table create. Must be called under _lock.
        if self._con is None:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.executescript(_SCHEMA_SQL)
            con.commit()
            self._con = con
        return self._con

    def add(self, title: str, notes: str = "") -> Task:
        task = Task(
            id=uuid4().hex[:8],
            title=title,
            notes=notes,
            done=False,
            created_at=datetime.now(UTC),
        )
        with self._lock:
            con = self._conn()
            con.execute(
                "INSERT INTO tasks (id, title, notes, done, created_at) VALUES (?, ?, ?, ?, ?)",
                (task.id, task.title, task.notes, int(task.done), task.created_at.isoformat()),
            )
            con.commit()
        return task

    def all(self) -> list[Task]:
        with self._lock:
            rows = self._conn().execute("SELECT * FROM tasks ORDER BY created_at, id").fetchall()
        return [_row_to_task(r) for r in rows]

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._conn().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row is not None else None

    def complete(self, task_id: str) -> bool:
        with self._lock:
            con = self._conn()
            cur = con.execute("UPDATE tasks SET done = 1 WHERE id = ?", (task_id,))
            con.commit()
            return cur.rowcount > 0

    def edit(
        self,
        task_id: str,
        *,
        title: str | None = None,
        notes: str | None = None,
    ) -> bool:
        with self._lock:
            con = self._conn()
            exists = con.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if exists is None:
                return False
            sets: list[str] = []
            params: list[Any] = []
            if title is not None:
                sets.append("title = ?")
                params.append(title)
            if notes is not None:
                sets.append("notes = ?")
                params.append(notes)
            if sets:  # a no-field edit is a no-op success (matches prior behavior)
                params.append(task_id)
                con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
                con.commit()
            return True

    def remove(self, task_id: str) -> bool:
        with self._lock:
            con = self._conn()
            cur = con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            con.commit()
            return cur.rowcount > 0


def make_tasks_tools(store: TaskStore) -> list[ToolDefinition]:
    async def tasks_add(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        task = store.add(
            title=str(args["title"]),
            notes=str(args.get("notes", "")),
        )
        return ToolResult(
            output={"id": task.id, "title": task.title},
            additional_tags=_PERSONAL_TAGS,
        )

    async def tasks_list(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        include_done = bool(args.get("include_done", False))
        items = [t for t in store.all() if include_done or not t.done]
        return ToolResult(
            output={
                "tasks": [{"id": t.id, "title": t.title, "done": t.done} for t in items],
            },
            additional_tags=_PERSONAL_TAGS,
        )

    async def tasks_complete(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        ok = store.complete(str(args["id"]))
        return ToolResult(output={"completed": ok}, additional_tags=_PERSONAL_TAGS)

    async def tasks_edit(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        ok = store.edit(
            str(args["id"]),
            title=args.get("title"),
            notes=args.get("notes"),
        )
        return ToolResult(output={"edited": ok}, additional_tags=_PERSONAL_TAGS)

    async def tasks_delete(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        ok = store.remove(str(args["id"]))
        return ToolResult(output={"deleted": ok}, additional_tags=_PERSONAL_TAGS)

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
            inherent_tags=_PERSONAL_TAGS,
            parameters_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        ),
    ]
