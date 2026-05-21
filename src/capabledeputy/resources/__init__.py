"""Operator-published resources.

Resources are documents the operator pre-publishes for the AI agent
to consume. Different shape than tools:

  - **Tools** are model-controlled. The LLM decides when to call them.
  - **Resources** are application-driven. The host (CapableDeputy)
    lists them; the agent pulls specific ones through the chokepoint.

Common use cases:
  - "My CV" — the agent has it available when drafting cover letters
  - "Style guide" — the agent uses it when composing prose
  - "Current project brief" — the agent has context about active work

Today this module ships one publisher: StaticResourcePublisher reads
operator-declared resources from configs/resources.yaml. Future
publishers could consume resources/list from upstream MCP servers
(spec 004 P1) or yield resources backed by computed sources.

The chokepoint mediates resources.list and resources.read same as any
other tool call — operator's bindings/policy decide what the agent
sees and can read.
"""

from capabledeputy.resources.static import (
    Resource,
    ResourceError,
    StaticResourcePublisher,
    load_static_resources,
)

__all__ = [
    "Resource",
    "ResourceError",
    "StaticResourcePublisher",
    "load_static_resources",
]
