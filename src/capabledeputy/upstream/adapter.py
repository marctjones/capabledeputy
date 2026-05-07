"""LabeledMcpAdapter: wraps a connected upstream MCP ClientSession and
registers its tools in CapableDeputy's ToolRegistry as policy-gated
wrappers.

Each upstream tool becomes a `ToolDefinition` whose handler proxies
calls to the upstream server via `session.call_tool` and returns a
`ToolResult` carrying the upstream server's inherent labels (so the
calling session inherits, e.g., `untrusted.external` for a fetch
server).

Subprocess lifecycle (spawning + connecting to upstream MCP servers
over stdio) lives in `manager.py`. This module is the security
wrapper layer; `manager.py` owns process management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from capabledeputy.upstream.config import UpstreamServerConfig

if TYPE_CHECKING:
    from mcp import ClientSession


def _infer_capability_kind(
    annotations: Any | None,
    name: str,
) -> CapabilityKind:
    """Best-effort mapping from upstream tool to capability kind.

    Hints used:
      - readOnlyHint=True ⇒ READ_FS (could refine: WEB_FETCH if name
        matches fetch/get patterns).
      - destructiveHint=True or writeRelated names ⇒ WRITE_FS.
      - "send"/"email" ⇒ SEND_EMAIL.
      - "fetch"/"web"/"http" ⇒ WEB_FETCH.
      - "calendar" ⇒ CALENDAR_*.
      - "purchase"/"buy" ⇒ QUEUE_PURCHASE.
      - default fallback ⇒ READ_FS (safest).
    """
    lowered = name.lower()
    if any(token in lowered for token in ("send", "email", "mail")):
        return CapabilityKind.SEND_EMAIL
    if any(token in lowered for token in ("fetch", "web", "http", "url")):
        return CapabilityKind.WEB_FETCH
    if "calendar" in lowered:
        if annotations is not None and getattr(annotations, "readOnlyHint", False):
            return CapabilityKind.CALENDAR_READ
        return CapabilityKind.CALENDAR_WRITE
    if any(token in lowered for token in ("purchase", "buy", "checkout")):
        return CapabilityKind.QUEUE_PURCHASE
    if annotations is not None and getattr(annotations, "readOnlyHint", False):
        return CapabilityKind.READ_FS
    if annotations is not None and getattr(annotations, "destructiveHint", False):
        return CapabilityKind.WRITE_FS
    return CapabilityKind.READ_FS


def _extract_labels(annotations_meta: dict[str, Any] | None) -> frozenset[Label]:
    if not annotations_meta:
        return frozenset()
    raw = annotations_meta.get("io.capabledeputy/inherent_labels", [])
    if not isinstance(raw, list):
        return frozenset()
    out: set[Label] = set()
    for v in raw:
        try:
            out.add(Label(str(v)))
        except ValueError:
            continue
    return frozenset(out)


class LabeledMcpAdapter:
    """Wraps a connected ClientSession; registers wrapped tools in a registry."""

    def __init__(
        self,
        config: UpstreamServerConfig,
        session: ClientSession,
    ) -> None:
        self._config = config
        self._session = session
        self._registered_names: list[str] = []

    @property
    def name(self) -> str:
        return self._config.name

    async def register_tools(self, registry: ToolRegistry) -> list[str]:
        """Discover upstream tools and register wrappers; return registered names."""
        listed = await self._session.list_tools()
        for upstream_tool in listed.tools:
            name = f"{self._config.name}.{upstream_tool.name}"
            override = self._config.tool_overrides.get(upstream_tool.name)

            kind = (
                override.capability_kind
                if override and override.capability_kind
                else _infer_capability_kind(
                    upstream_tool.annotations,
                    upstream_tool.name,
                )
            )
            additional = override.additional_labels if override else frozenset()
            inherent = (
                self._config.inherent_labels | additional | _extract_labels(upstream_tool.meta)
            )

            registry.register(
                ToolDefinition(
                    name=name,
                    description=(
                        upstream_tool.description or f"Upstream tool from {self._config.name}"
                    ),
                    capability_kind=kind,
                    handler=self._make_handler(upstream_tool.name),
                    inherent_labels=inherent,
                    parameters_schema=upstream_tool.inputSchema or {"type": "object"},
                ),
            )
            self._registered_names.append(name)
        return list(self._registered_names)

    def _make_handler(self, upstream_name: str):
        async def handler(args: dict[str, Any], context: ToolContext) -> ToolResult:
            result = await self._session.call_tool(upstream_name, arguments=args)
            structured = getattr(result, "structuredContent", None)
            if isinstance(structured, dict):
                output = structured
            else:
                texts: list[str] = []
                for block in result.content:
                    text = getattr(block, "text", None)
                    if text is not None:
                        texts.append(text)
                output = {"text": "\n".join(texts)} if texts else {}

            additional: frozenset[Label] = frozenset()
            if getattr(result, "isError", False):
                output = {"upstream_error": True, **output}

            return ToolResult(output=output, additional_labels=additional)

        return handler
