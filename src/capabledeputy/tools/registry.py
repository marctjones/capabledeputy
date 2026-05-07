"""Tool definitions and registry.

A ToolDefinition pairs a tool name with the metadata the policy engine
needs (capability kind, inherent labels, how to extract target/amount
from call args) plus an async handler that does the actual work. The
registry holds these so the dispatcher can look them up by name.

Handlers receive a ToolContext with the calling session's id and label
set, and return a ToolResult whose `additional_labels` are unioned into
the session's label set after the call (in addition to the tool's
declared `inherent_labels`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label


@dataclass(frozen=True)
class ToolContext:
    session_id: UUID
    label_set: frozenset[Label]


@dataclass(frozen=True)
class ToolResult:
    output: dict[str, Any]
    additional_labels: frozenset[Label] = field(default_factory=frozenset)


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    capability_kind: CapabilityKind
    handler: ToolHandler
    target_arg: str = "target"
    amount_arg: str | None = None
    inherent_labels: frozenset[Label] = field(default_factory=frozenset)
    parameters_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []},
    )

    def extract_target(self, args: dict[str, Any]) -> str:
        return str(args.get(self.target_arg, ""))

    def extract_amount(self, args: dict[str, Any]) -> int | None:
        if self.amount_arg is None:
            return None
        value = args.get(self.amount_arg)
        return int(value) if value is not None else None


class DuplicateToolError(ValueError):
    pass


class ToolNotFoundError(KeyError):
    def __init__(self, name: str) -> None:
        super().__init__(f"tool not found: {name}")
        self.name = name


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise DuplicateToolError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as e:
            raise ToolNotFoundError(name) from e

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
