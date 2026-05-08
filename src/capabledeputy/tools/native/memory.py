"""Labeled in-memory key-value store and the memory.read / memory.write tools.

memory.write stores a value alongside the calling session's current label
set. memory.read returns the value along with the stored labels as
additional_labels, so they propagate into the calling session — that's
the IFC-correct behavior: reading labeled data inherits its labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


@dataclass
class _MemoryEntry:
    value: Any
    labels: frozenset[Label]


class LabeledMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, _MemoryEntry] = {}

    def write(self, key: str, value: Any, labels: frozenset[Label]) -> None:
        self._data[key] = _MemoryEntry(value=value, labels=labels)

    def read(self, key: str) -> _MemoryEntry | None:
        return self._data.get(key)

    def keys(self) -> list[str]:
        return sorted(self._data.keys())

    def labels_of(self, key: str) -> frozenset[Label]:
        entry = self._data.get(key)
        return entry.labels if entry else frozenset()


def make_memory_tools(store: LabeledMemoryStore) -> list[ToolDefinition]:
    async def memory_write(args: dict[str, Any], context: ToolContext) -> ToolResult:
        key = str(args["key"])
        value = args["value"]
        store.write(key, value, context.label_set)
        return ToolResult(output={"ok": True, "key": key})

    async def memory_read(args: dict[str, Any], context: ToolContext) -> ToolResult:
        key = str(args["key"])
        entry = store.read(key)
        if entry is None:
            return ToolResult(output={"found": False})
        return ToolResult(
            output={"found": True, "value": entry.value},
            additional_labels=entry.labels,
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
        store.write(key, value, context.label_set)
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
        store.write(key, value, context.label_set)
        return ToolResult(output={"ok": True, "key": key, "modified": True})

    async def memory_delete(args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Remove a key from the store. Tagged DELETE_FS — the
        destructive-op gate fires unless explicitly authorized."""
        key = str(args["key"])
        if store.read(key) is None:
            return ToolResult(
                output={"ok": False, "error": f"key does not exist: {key}"},
            )
        # The store doesn't currently expose a delete primitive; we
        # emulate it by overwriting with a tombstone marker. A future
        # store implementation should add a real delete that removes
        # the entry. For now this surfaces the right policy semantics.
        store._data.pop(key, None)  # type: ignore[attr-defined]
        return ToolResult(output={"ok": True, "key": key, "deleted": True})

    return [
        ToolDefinition(
            name="memory.write",
            description=(
                "Write a value to a key in the memory store (create or "
                "overwrite). Required args: key (string), value (string)."
            ),
            capability_kind=CapabilityKind.WRITE_FS,
            handler=memory_write,
            target_arg="key",
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
            description=(
                "Read the value at a key in the memory store. Returns "
                "{found, value} and propagates the value's labels into "
                "the calling session. Required args: key (string)."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=memory_read,
            target_arg="key",
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to read."},
                },
                "required": ["key"],
            },
        ),
        ToolDefinition(
            name="memory.create",
            description=(
                "Create a new key. Fails if the key already exists. "
                "Non-destructive: bypasses the destructive-op gate. "
                "Required args: key (string), value (string)."
            ),
            capability_kind=CapabilityKind.CREATE_FS,
            handler=memory_create,
            target_arg="key",
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
            description=(
                "Update an existing key. Fails if the key doesn't exist. "
                "Destructive: requires approval unless the capability has "
                "allows_destructive=True. Required args: key, value."
            ),
            capability_kind=CapabilityKind.MODIFY_FS,
            handler=memory_update,
            target_arg="key",
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
            description=(
                "Remove a key from the memory store. Destructive: "
                "requires approval unless the capability has "
                "allows_destructive=True. Required args: key."
            ),
            capability_kind=CapabilityKind.DELETE_FS,
            handler=memory_delete,
            target_arg="key",
            parameters_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        ),
    ]
