"""Labeled key-value memory store and the memory.read / memory.write tools.

memory.write stores a value alongside the calling session's current label
state. memory.read returns the value along with the stored labels as
additional_tags, so they propagate into the calling session — that's
the IFC-correct behavior: reading labeled data inherits its labels.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.route import ApprovalPayloadKind, ApprovalRoute
from capabledeputy.patterns.reference_handle import ResolvedLabels
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import LabelState
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

_DESTRUCTIVE_ROUTE = ApprovalRoute(
    action=ApprovalAction.EXECUTE_DESTRUCTIVE,
    target_arg="key",
    payload_kind=ApprovalPayloadKind.TOOL_ENVELOPE,
)


@dataclass
class _MemoryEntry:
    value: Any
    label_state: LabelState


class LabeledMemoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path
        self._data: dict[str, _MemoryEntry] = {}
        self._lock = RLock()
        self._initialized = False

    @property
    def durable(self) -> bool:
        return self._db_path is not None

    def write(self, key: str, value: Any, label_state: LabelState | frozenset[Any]) -> None:
        normalized_label_state = _normalize_label_state(label_state)
        if self._db_path is None:
            self._data[key] = _MemoryEntry(value=value, label_state=normalized_label_state)
            return
        self._ensure_initialized()
        now = _utcnow_iso()
        encoded_value = json.dumps(value, separators=(",", ":"))
        encoded_labels = json.dumps(normalized_label_state.to_dict(), separators=(",", ":"))
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO memory_entries (key, value_json, label_state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    label_state = excluded.label_state,
                    updated_at = excluded.updated_at
                """,
                (key, encoded_value, encoded_labels, now, now),
            )

    def read(self, key: str) -> _MemoryEntry | None:
        if self._db_path is None:
            return self._data.get(key)
        self._ensure_initialized()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT value_json, label_state FROM memory_entries WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return _MemoryEntry(
            value=json.loads(row["value_json"]),
            label_state=LabelState.from_dict(json.loads(row["label_state"])),
        )

    def keys(self) -> list[str]:
        if self._db_path is None:
            return sorted(self._data.keys())
        self._ensure_initialized()
        with self._connection() as conn:
            rows = conn.execute("SELECT key FROM memory_entries ORDER BY key").fetchall()
        return [str(row["key"]) for row in rows]

    def label_state_of(self, key: str) -> LabelState:
        entry = self.read(key)
        return entry.label_state if entry else LabelState()

    def labels_of(self, key: str) -> LabelState:
        """Alias for label_state_of (backward compat)."""
        return self.label_state_of(key)

    def snapshot(self) -> dict[str, Any]:
        keys = self.keys()
        return {
            "durable": self.durable,
            "path": str(self._db_path) if self._db_path is not None else None,
            "entry_count": len(keys),
            "keys": keys,
            "labels_by_key": {
                key: self.label_state_of(key).to_dict() for key in keys
            },
        }

    def delete(self, key: str) -> bool:
        if self._db_path is None:
            return self._data.pop(key, None) is not None
        self._ensure_initialized()
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM memory_entries WHERE key = ?", (key,))
            return cursor.rowcount > 0

    def _ensure_initialized(self) -> None:
        if self._db_path is None or self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_entries (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        label_state TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """,
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memory_entries_updated "
                    "ON memory_entries(updated_at)",
                )
            self._initialized = True

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._db_path is None:
            raise RuntimeError("memory store is not configured for SQLite")
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()


def _resolved_labels_from_state(label_state: LabelState) -> ResolvedLabels:
    """Encode a LabelState snapshot in the reference-handle wire format."""
    axis_a = tuple(
        f"{tag.category}:{tag.tier.value}"
        for tag in sorted(label_state.a, key=lambda t: (t.category, t.tier.value))
    )
    axis_b = tuple(tag.level.value for tag in sorted(label_state.b, key=lambda t: t.level.value))
    return ResolvedLabels(axis_a=axis_a, axis_b=axis_b)


def make_memory_tools(store: LabeledMemoryStore) -> list[ToolDefinition]:
    async def memory_write(args: dict[str, Any], context: ToolContext) -> ToolResult:
        key = str(args["key"])
        value = args["value"]
        store.write(key, value, context.label_state)
        return ToolResult(output={"ok": True, "key": key})

    async def memory_read(args: dict[str, Any], context: ToolContext) -> ToolResult:
        key = str(args["key"])
        entry = store.read(key)
        if entry is None:
            return ToolResult(output={"found": False})
        return ToolResult(
            output={"found": True, "value": entry.value},
            additional_tags=entry.label_state,
        )

    async def memory_handle(args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Return a Pattern (3) handle for a stored value without exposing it."""
        key = str(args["key"])
        entry = store.read(key)
        if entry is None:
            return ToolResult(output={"found": False})
        if context.handle_store is None:
            return ToolResult(
                output={
                    "found": True,
                    "error": "reference handle store is not wired",
                },
            )
        handle = context.handle_store.issue(
            context.session_id,
            entry.value,
            _resolved_labels_from_state(entry.label_state),
        )
        return ToolResult(
            output={
                "found": True,
                "key": key,
                "handle": str(handle.id),
            },
        )

    async def memory_create(args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Create-only write. Fails if the key already exists. Tagged
        CREATE_FS so the policy engine's destructive-op gate doesn't
        fire — creating a new key is non-destructive by definition."""
        key = str(args["key"])
        value = args["value"]
        if store.read(key) is not None:
            return ToolResult(
                output={"ok": False, "error": f"key already exists: {key}"},
            )
        store.write(key, value, context.label_state)
        return ToolResult(output={"ok": True, "key": key, "created": True})

    async def memory_update(args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Modify-existing write. Fails if the key doesn't exist.
        Tagged MODIFY_FS — the destructive-op gate fires unless the
        capability has allows_destructive=True or the user approves."""
        key = str(args["key"])
        value = args["value"]
        if store.read(key) is None:
            return ToolResult(
                output={"ok": False, "error": f"key does not exist: {key}"},
            )
        store.write(key, value, context.label_state)
        return ToolResult(output={"ok": True, "key": key, "modified": True})

    async def memory_delete(args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Remove a key from the store. Tagged DELETE_FS — the
        destructive-op gate fires unless explicitly authorized."""
        key = str(args["key"])
        if store.read(key) is None:
            return ToolResult(
                output={"ok": False, "error": f"key does not exist: {key}"},
            )
        store.delete(key)
        return ToolResult(output={"ok": True, "key": key, "deleted": True})

    return [
        ToolDefinition(
            name="memory.write",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="memory.write"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            description=(
                "Write a value to a key in the memory store (create or "
                "overwrite). Required args: key (string), value (string)."
            ),
            capability_kind=CapabilityKind.WRITE_FS,
            handler=memory_write,
            target_arg="key",
            effect_class="data.write_local",
            default_reversibility={"degree": "reversible-with-friction", "agent": "human"},
            tool_provenance="operator-curated",
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to write."},
                    "value": {"type": "string", "description": "Value to store."},
                },
                "required": ["key", "value"],
            },
        ),
        ToolDefinition(
            name="memory.read",
            operations=(Operation(EffectClass.FETCH, subtype="memory.read"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            description=(
                "Read the value at a key in the memory store. Returns "
                "{found, value} and propagates the value's labels into "
                "the calling session. Raw reads are refused for restricted "
                "or prohibited source labels; use memory.handle plus a "
                "handle-aware tool or sealed isolation for those values. "
                "Required args: key (string)."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=memory_read,
            target_arg="key",
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            source_label_lookup=lambda args: store.label_state_of(str(args.get("key", ""))),
            forbid_restricted_source=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to read."},
                },
                "required": ["key"],
            },
        ),
        ToolDefinition(
            name="memory.handle",
            operations=(Operation(EffectClass.FETCH, subtype="memory.handle"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            description=(
                "Issue a planner-safe reference handle for the value at a "
                "memory key. The raw value stays in the runtime-private "
                "handle store; the planner receives only {found, key, "
                "handle}. Use this for restricted/prohibited memory values "
                "that must flow into handle-aware tools. Required args: "
                "key (string)."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=memory_handle,
            target_arg="key",
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            source_label_lookup=lambda args: store.label_state_of(str(args.get("key", ""))),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to wrap."},
                },
                "required": ["key"],
            },
        ),
        ToolDefinition(
            name="memory.create",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="memory.create"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            description=(
                "Create a new key. Fails if the key already exists. "
                "Non-destructive: bypasses the destructive-op gate. "
                "Required args: key (string), value (string)."
            ),
            capability_kind=CapabilityKind.CREATE_FS,
            handler=memory_create,
            target_arg="key",
            effect_class="data.create_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        ),
        ToolDefinition(
            name="memory.update",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="memory.update"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            description=(
                "Update an existing key. Fails if the key doesn't exist. "
                "Destructive: requires approval unless the capability has "
                "allows_destructive=True. Required args: key, value."
            ),
            capability_kind=CapabilityKind.MODIFY_FS,
            effect_class="data.modify_local",
            default_reversibility={"degree": "reversible-with-friction", "agent": "human"},
            tool_provenance="operator-curated",
            handler=memory_update,
            target_arg="key",
            approval_route=_DESTRUCTIVE_ROUTE,
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        ),
        ToolDefinition(
            name="memory.delete",
            operations=(Operation(EffectClass.DESTROY, subtype="memory.delete"),),
            risk_ids=("RISK-DESTRUCTIVE-WRITE",),
            surfaces_destination_id=True,
            effect_class="data.delete_local",
            default_reversibility={"degree": "irreversible", "agent": "external"},
            tool_provenance="operator-curated",
            description=(
                "Remove a key from the memory store. Destructive: "
                "requires approval unless the capability has "
                "allows_destructive=True. Required args: key."
            ),
            capability_kind=CapabilityKind.DELETE_FS,
            handler=memory_delete,
            target_arg="key",
            approval_route=_DESTRUCTIVE_ROUTE,
            parameters_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        ),
    ]


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_label_state(label_state: LabelState | frozenset[Any]) -> LabelState:
    if isinstance(label_state, LabelState):
        return label_state
    if isinstance(label_state, frozenset) and not label_state:
        return LabelState()
    raise TypeError("memory labels must be a LabelState")
