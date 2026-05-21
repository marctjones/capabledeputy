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


_DELETE_TOKENS = ("delete", "remove", "unlink", "rmdir", "destroy", "purge")
_MODIFY_TOKENS = ("write", "update", "modify", "edit", "patch", "replace", "set", "append")
_CREATE_TOKENS = ("create", "new", "add", "mkdir")


def _infer_capability_kind(
    annotations: Any | None,
    name: str,
) -> CapabilityKind | None:
    """Confidence-based mapping from an upstream tool to a capability
    kind. Returns the kind ONLY when the tool is confidently
    classifiable; returns ``None`` when it is not, so the caller can
    fail closed (strict) or fall back (non-strict).

    Security-relevant choices:
      - destructive/modify/delete names and ``destructiveHint`` map to
        the GRANULAR destructive kinds (MODIFY_*/DELETE_*), never the
        legacy ``WRITE_FS`` union — otherwise the policy engine's
        destructive-op gate would be silently bypassed.
      - an unrecognised tool returns ``None`` (no permissive default).
    """
    lowered = name.lower()
    read_only = annotations is not None and getattr(annotations, "readOnlyHint", False)
    destructive = annotations is not None and getattr(
        annotations,
        "destructiveHint",
        False,
    )

    if any(t in lowered for t in ("send", "email", "mail")):
        return CapabilityKind.SEND_EMAIL
    if any(t in lowered for t in ("fetch", "web", "http", "url", "browse")):
        return CapabilityKind.WEB_FETCH
    if any(t in lowered for t in ("purchase", "buy", "checkout", "order")):
        return CapabilityKind.QUEUE_PURCHASE

    if "calendar" in lowered or "event" in lowered:
        if read_only:
            return CapabilityKind.CALENDAR_READ
        if any(t in lowered for t in _DELETE_TOKENS):
            return CapabilityKind.DELETE_CAL
        if any(t in lowered for t in _CREATE_TOKENS):
            return CapabilityKind.CREATE_CAL
        if destructive or any(t in lowered for t in _MODIFY_TOKENS):
            return CapabilityKind.MODIFY_CAL
        return None

    if any(t in lowered for t in _DELETE_TOKENS):
        return CapabilityKind.DELETE_FS
    if any(t in lowered for t in _CREATE_TOKENS):
        return CapabilityKind.CREATE_FS
    if destructive or any(t in lowered for t in _MODIFY_TOKENS):
        return CapabilityKind.MODIFY_FS
    if read_only or any(t in lowered for t in ("read", "get", "list", "search", "find")):
        return CapabilityKind.READ_FS
    return None


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
        self._rejected_tools: list[str] = []

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def rejected_tools(self) -> list[str]:
        """Upstream tools refused registration under strict mode
        (unclassifiable, no override). Surfaced for audit/observability."""
        return list(self._rejected_tools)

    async def list_upstream_resources(self) -> list[dict[str, Any]]:
        """Spec 004 P1 — discover the upstream server's resources catalog.

        Returns a list of {uri, name, description, mime_type, labels}
        dicts in the same shape as our StaticResourcePublisher's catalog
        entries. Labels are derived from the upstream config's
        inherent_labels plus any meta-supplied labels on each resource.

        Gracefully handles servers that don't support resources/list —
        returns an empty list instead of raising.
        """
        try:
            listed = await self._session.list_resources()
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for r in getattr(listed, "resources", []):
            uri = str(getattr(r, "uri", ""))
            if not uri:
                continue
            meta_labels = _extract_labels(getattr(r, "meta", None))
            all_labels = self._config.inherent_labels | meta_labels
            out.append(
                {
                    "uri": uri,
                    "name": str(getattr(r, "name", "") or uri),
                    "description": str(getattr(r, "description", "") or ""),
                    "mime_type": str(getattr(r, "mimeType", "") or "text/plain"),
                    "labels": sorted(label.value for label in all_labels),
                    "server": self._config.name,
                },
            )
        return out

    async def read_upstream_resource(self, uri: str) -> dict[str, Any]:
        """Read a resource from the upstream server.

        Returns {found, uri, content, mime_type, labels} same shape as
        the native resources.read tool. Inherent labels propagate per
        the upstream config.
        """
        try:
            result = await self._session.read_resource(uri)
        except Exception as e:
            return {"found": False, "uri": uri, "error": str(e)}
        contents = getattr(result, "contents", [])
        # MCP returns a list of content blocks; collect text where
        # available.
        texts: list[str] = []
        for c in contents:
            t = getattr(c, "text", None)
            if t is not None:
                texts.append(str(t))
        return {
            "found": True,
            "uri": uri,
            "content": "\n".join(texts),
            "mime_type": str(
                getattr(contents[0], "mimeType", "text/plain") if contents else "text/plain"
            ),
            "labels": sorted(label.value for label in self._config.inherent_labels),
            "server": self._config.name,
        }

    async def register_tools(self, registry: ToolRegistry) -> list[str]:
        """Discover upstream tools and register wrappers; return registered names."""
        listed = await self._session.list_tools()
        for upstream_tool in listed.tools:
            name = f"{self._config.name}.{upstream_tool.name}"
            override = self._config.tool_overrides.get(upstream_tool.name)

            kind: CapabilityKind | None
            if override and override.capability_kind:
                kind = override.capability_kind
            else:
                kind = _infer_capability_kind(
                    upstream_tool.annotations,
                    upstream_tool.name,
                )

            if kind is None:
                # Not confidently classifiable and no explicit override.
                if self._config.strict:
                    # Fail closed: refuse to register. An unmapped tool
                    # is unavailable, never silently granted READ_FS.
                    self._rejected_tools.append(upstream_tool.name)
                    continue
                # Legacy/trusted server opted out of strict: most-
                # restrictive fallback (read), not write/exec.
                kind = CapabilityKind.READ_FS

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
