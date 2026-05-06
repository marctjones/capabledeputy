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

    return [
        ToolDefinition(
            name="memory.write",
            description="Write a labeled value to the memory store",
            capability_kind=CapabilityKind.WRITE_FS,
            handler=memory_write,
            target_arg="key",
        ),
        ToolDefinition(
            name="memory.read",
            description="Read a labeled value (and its labels) from the memory store",
            capability_kind=CapabilityKind.READ_FS,
            handler=memory_read,
            target_arg="key",
        ),
    ]
