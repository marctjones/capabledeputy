"""MCP Resources surface for CapableDeputy memory entries.

Each labeled memory entry is exposed as a resource at
`capdep://memory/{key}`. The resource's `_meta` carries the entry's
labels under `io.capabledeputy/labels` so MCP hosts can see what
sensitivity tags are attached *before* reading. Reads dispatch
through the same `LabeledToolClient` path that `memory.read` uses,
so policy gating and label propagation are identical to the tool
surface.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import mcp.types as mcp_types

from capabledeputy.ipc.client import DaemonClient

MEMORY_URI_PREFIX = "capdep://memory/"


class ResourceAccessError(RuntimeError):
    pass


async def list_resources(client: DaemonClient) -> list[mcp_types.Resource]:
    result = await client.call("memory.entries")
    resources: list[mcp_types.Resource] = []
    for entry in result["entries"]:
        labels: list[str] = entry["labels"]
        meta: dict[str, Any] = {"io.capabledeputy/labels": labels}
        description = f"Labeled memory entry. Labels: {', '.join(labels) if labels else '(none)'}"
        resources.append(
            mcp_types.Resource(
                uri=f"{MEMORY_URI_PREFIX}{entry['key']}",
                name=f"memory:{entry['key']}",
                title=entry["key"],
                description=description,
                mimeType="application/json",
                **{"_meta": meta},
            ),
        )
    return resources


async def read_resource(
    client: DaemonClient,
    session_id: UUID,
    uri: str,
) -> str:
    if not uri.startswith(MEMORY_URI_PREFIX):
        raise ResourceAccessError(f"unsupported URI scheme: {uri}")
    key = uri[len(MEMORY_URI_PREFIX) :]
    if not key:
        raise ResourceAccessError("empty memory key in URI")

    result = await client.call(
        "tool.call",
        {
            "session_id": str(session_id),
            "tool": "memory.read",
            "args": {"key": key},
        },
    )

    if result.get("error"):
        raise ResourceAccessError(f"tool error: {result['error']}")
    if result["decision"] != "allow":
        raise ResourceAccessError(
            f"policy denied (rule={result.get('rule')}): {result.get('reason', '')}",
        )

    output = result.get("output") or {}
    if not output.get("found", False):
        raise ResourceAccessError(f"memory key not found: {key}")
    return json.dumps(output, indent=2)
