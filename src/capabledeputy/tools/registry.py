"""Tool definitions and registry.

A ToolDefinition pairs a tool name with the metadata the policy engine
needs (capability kind, inherent labels, how to extract target/amount
from call args) plus an async handler that does the actual work. The
registry holds these so the dispatcher can look them up by name.

In Phase 3a all tools are in-process. Phase 3+ will introduce an
mcp-SDK-backed handler that lets upstream MCP servers register through
the same protocol (DESIGN.md §10.7).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    capability_kind: CapabilityKind
    handler: ToolHandler
    target_arg: str = "target"
    amount_arg: str | None = None
    inherent_labels: frozenset[Label] = field(default_factory=frozenset)

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
