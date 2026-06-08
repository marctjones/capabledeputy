"""LabeledMcpAdapter: wraps a connected upstream MCP ClientSession and
registers its tools in CapableDeputy's ToolRegistry as policy-gated
wrappers.

Each upstream tool becomes a `ToolDefinition` whose handler proxies
calls to the upstream server via `session.call_tool` and returns a
`ToolResult` carrying the upstream server's inherent tags (so the
calling session inherits, e.g., external provenance for a fetch
server).

Subprocess lifecycle (spawning + connecting to upstream MCP servers
over stdio) lives in `manager.py`. This module is the security
wrapper layer; `manager.py` owns process management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import AnyUrl

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceTag,
    most_restrictive_inherit,
)
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    default_operation_for_kind,
)
from capabledeputy.upstream.config import UpstreamServerConfig

if TYPE_CHECKING:
    from mcp import ClientSession

    # The adapter accepts either a raw mcp ClientSession (older tests
    # / standalone use) or a crash-recovering `LiveSession` wrapper
    # (the production path through UpstreamManager). Both quack the
    # same: list_tools/list_resources/read_resource/call_tool.
    from capabledeputy.upstream.supervisor import LiveSession

    SessionLike = ClientSession | LiveSession
else:
    SessionLike = object  # only for type hints


_DELETE_TOKENS = ("delete", "remove", "unlink", "rmdir", "destroy", "purge")
_MODIFY_TOKENS = ("write", "update", "modify", "edit", "patch", "replace", "set", "append")
_CREATE_TOKENS = ("create", "new", "add", "mkdir")


# Per-tool-response output cap. A single oversized response from an
# upstream MCP server (e.g. a 100 KB gmail message with full HTML
# body + DKIM/ARC headers) can blow the LLM context window in a few
# parallel calls — see the email-summary failure mode where 5
# parallel gmail_messages_get calls pushed the prompt to 202k tokens
# against a 200k cap. Native tools (fs, fetch, git) already truncate;
# the upstream adapter is the missing piece.
#
# 32 KB caps a single response to ~8k tokens, so even 8 parallel
# oversized calls stay under 64k tokens of tool output — well within
# any modern context window. The LLM gets a `truncated: true` marker
# + `original_size_bytes` so it knows to re-request a narrower view
# (e.g. format=metadata) rather than treat the truncated text as
# complete.
MAX_UPSTREAM_TOOL_OUTPUT_BYTES = 32 * 1024


def _tag_to_str(tag: CategoryTag | ProvenanceTag) -> str:
    """Convert a CategoryTag or ProvenanceTag to a string representation."""
    if isinstance(tag, CategoryTag):
        return tag.category
    elif isinstance(tag, ProvenanceTag):
        return tag.level.value
    return ""


def _maybe_truncate_output(output: dict[str, Any]) -> dict[str, Any]:
    """Cap the `text` field of an upstream tool result to keep a
    single response from blowing the context window. Structured
    outputs without a `text` field pass through untouched (they're
    typically small dicts that the LLM consumes wholesale).

    When truncating, replace `text` with the head of the original
    plus a hint, and set `truncated=True` + `original_size_bytes` so
    the LLM has the signal to take a different approach next call.
    """
    text = output.get("text")
    if not isinstance(text, str):
        return output
    raw = text.encode("utf-8")
    if len(raw) <= MAX_UPSTREAM_TOOL_OUTPUT_BYTES:
        return output
    head = raw[:MAX_UPSTREAM_TOOL_OUTPUT_BYTES].decode("utf-8", errors="ignore")
    hint = (
        f"\n\n[truncated: response was {len(raw):,} bytes; "
        f"capped at {MAX_UPSTREAM_TOOL_OUTPUT_BYTES:,}. "
        "If you need the full payload, request a single item at a "
        "time or use a list/metadata variant of this tool.]"
    )
    return {
        **output,
        "text": head + hint,
        "truncated": True,
        "original_size_bytes": len(raw),
    }


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

    # Read-operation tokens that disambiguate read vs. write/send for
    # services where the service-name alone is ambiguous (a 'gmail'
    # tool can be read OR send; a 'drive' tool can be read OR delete).
    read_tokens = ("read", "get", "list", "search", "find", "view")
    has_read_token = any(t in lowered for t in read_tokens)

    # Service classification — first match wins. Each branch then
    # picks the right read/write kind based on tokens + hints.

    # Gmail (matches "gmail.*" but NOT just "mail" — otherwise
    # "voicemail" / "mailbox" / etc. would be misclassified).
    if "gmail" in lowered:
        if "send" in lowered:
            return CapabilityKind.SEND_EMAIL
        if any(t in lowered for t in _DELETE_TOKENS):
            return CapabilityKind.DELETE_FS  # email deletion — destructive
        if read_only or has_read_token:
            return CapabilityKind.GMAIL_READ
        # Default Gmail tool with unclear hint: most-restrictive read.
        return CapabilityKind.GMAIL_READ

    # Generic email (IMAP, SMTP — not Gmail-specific). "email" /
    # "imap" / "smtp" in the name.
    if any(t in lowered for t in ("imap", "smtp")) or (
        "email" in lowered and "gmail" not in lowered
    ):
        if "send" in lowered or "smtp" in lowered:
            return CapabilityKind.SEND_EMAIL
        # IMAP-specific read tokens. `fetch` is the IMAP read primitive.
        if read_only or has_read_token or "fetch" in lowered:
            return CapabilityKind.IMAP_READ
        # IMAP tools without a clear hint default to read (most-
        # restrictive); destructive operations should be explicitly
        # marked via tool annotations.
        return CapabilityKind.IMAP_READ

    # Google Drive (matches "drive.*"). Read by default; create/
    # modify/delete distinguished by tokens.
    if "drive" in lowered:
        if any(t in lowered for t in _DELETE_TOKENS):
            return CapabilityKind.DELETE_FS
        if any(t in lowered for t in _CREATE_TOKENS):
            return CapabilityKind.CREATE_FS
        if destructive or any(t in lowered for t in _MODIFY_TOKENS):
            return CapabilityKind.MODIFY_FS
        if read_only or has_read_token:
            return CapabilityKind.DRIVE_READ
        return CapabilityKind.DRIVE_READ

    # "send" alone (no Gmail) is generic SEND_EMAIL.
    if "send" in lowered:
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
    if read_only or has_read_token:
        return CapabilityKind.READ_FS
    return None


def _extract_tags(annotations_meta: dict[str, Any] | None) -> LabelState:
    if not annotations_meta:
        return LabelState()
    raw = annotations_meta.get("io.capabledeputy/inherent_tags", {})
    if not isinstance(raw, dict):
        return LabelState()
    return LabelState.from_dict(raw)


class LabeledMcpAdapter:
    """Wraps a connected ClientSession; registers wrapped tools in a registry."""

    def __init__(
        self,
        config: UpstreamServerConfig,
        session: SessionLike,
        result_labeler: Any = None,
    ) -> None:
        self._config = config
        self._session = session
        self._registered_names: list[str] = []
        self._rejected_tools: list[str] = []
        # Issue #34 — optional per-result labeler. Called as
        # result_labeler(tool_name, args, output) -> LabelState and merged
        # (raise-only) into the result's additional_tags. The email
        # labeler is wired here for Gmail-classed servers so incoming mail
        # gets per-message Axis-A category labels on top of the server's
        # inherent-tag floor. None ⇒ no per-result labeling.
        self._result_labeler = result_labeler

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def rejected_tools(self) -> list[str]:
        """Upstream tools refused registration under strict mode
        (unclassifiable, no override). Surfaced for audit/observability."""
        return list(self._rejected_tools)

    @property
    def registered_names(self) -> list[str]:
        """Tools that registered successfully (with capdep's `<server>.<tool>` prefix)."""
        return list(self._registered_names)

    async def list_upstream_resources(self) -> list[dict[str, Any]]:
        """Spec 004 P1 — discover the upstream server's resources catalog.

        Returns a list of {uri, name, description, mime_type, labels}
        dicts in the same shape as our StaticResourcePublisher's catalog
        entries. Labels are derived from the upstream config's
        inherent_tags plus any meta-supplied tags on each resource.

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
            meta_tags = _extract_tags(getattr(r, "meta", None))
            all_tags = most_restrictive_inherit(self._config.inherent_tags, meta_tags)
            out.append(
                {
                    "uri": uri,
                    "name": str(getattr(r, "name", "") or uri),
                    "description": str(getattr(r, "description", "") or ""),
                    "mime_type": str(getattr(r, "mimeType", "") or "text/plain"),
                    "labels": sorted(_tag_to_str(t) for t in all_tags.a | all_tags.b),
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
            result = await self._session.read_resource(AnyUrl(uri))
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
            "labels": sorted(
                _tag_to_str(t) for t in self._config.inherent_tags.a | self._config.inherent_tags.b
            ),
            "server": self._config.name,
        }

    async def register_tools(self, registry: ToolRegistry) -> list[str]:
        """Discover upstream tools and register wrappers; return registered names."""
        listed = await self._session.list_tools()
        for upstream_tool in listed.tools:
            # Operator hard-disable (fail-closed) — a tool named in the
            # config's `disabled_tools` is NEVER registered, regardless of
            # any override or name inference. This is how an operator
            # forbids a capability the upstream server exposes (e.g. Gmail
            # outbound `send_gmail_message`): it never enters the registry,
            # so the planner can't even propose it and no grant can enable
            # it. Checked BEFORE classification.
            if upstream_tool.name in self._config.disabled_tools:
                self._rejected_tools.append(upstream_tool.name)
                continue

            name = f"{self._config.name}.{upstream_tool.name}"
            override = self._config.tool_overrides.get(upstream_tool.name)

            kind: CapabilityKind | str | None
            if override and override.capability_kind:
                kind = override.capability_kind
            elif override is not None:
                # Issue #35 — `_OverrideWithCustomKind` carries an
                # unresolved custom-kind name (e.g. "slack:dm.send")
                # that wasn't a built-in. Try to resolve via the
                # global registry; if registered, use the string.
                raw = getattr(override, "_custom_kind_name", None)
                if raw:
                    from capabledeputy.policy.capabilities import (
                        UnknownKindError,
                        resolve_kind,
                    )

                    try:
                        kind = resolve_kind(raw)
                    except UnknownKindError:
                        kind = None
                else:
                    kind = _infer_capability_kind(
                        upstream_tool.annotations,
                        upstream_tool.name,
                    )
            else:
                kind = _infer_capability_kind(
                    upstream_tool.annotations,
                    upstream_tool.name,
                )

            if kind is None:
                # Not confidently classifiable and no explicit override.
                if self._config.strict:
                    # Fail closed: refuse to register. An unmapped tool
                    # is unavailable, never silently granted any read kind.
                    self._rejected_tools.append(upstream_tool.name)
                    continue
                # Legacy/trusted server opted out of strict: most-
                # restrictive fallback (READ_FS for back-compat — older
                # grants might cover this tool. New deployments should
                # use strict mode + explicit overrides).
                kind = CapabilityKind.READ_FS

            # Operator hard-disable by capability kind (name-independent) —
            # refuse any tool that resolved to a forbidden kind, however it
            # was classified. `disabled_kinds: {"SEND_EMAIL"}` forbids ALL
            # outbound mail from this server regardless of the send tool's
            # name. Compared on the kind's string value so built-in enums
            # and custom-kind strings both match.
            if self._config.disabled_kinds:
                kind_str = getattr(kind, "value", str(kind))
                if kind_str in self._config.disabled_kinds:
                    self._rejected_tools.append(upstream_tool.name)
                    continue

            additional = override.additional_tags if override else LabelState()
            inherent = most_restrictive_inherit(
                self._config.inherent_tags, additional, _extract_tags(upstream_tool.meta)
            )

            op, op_risks, op_surfaces = default_operation_for_kind(kind)
            registry.register(
                ToolDefinition(
                    name=name,
                    description=(
                        upstream_tool.description or f"Upstream tool from {self._config.name}"
                    ),
                    capability_kind=kind,
                    handler=self._make_handler(upstream_tool.name),
                    inherent_tags=inherent,
                    parameters_schema=upstream_tool.inputSchema or {"type": "object"},
                    operations=(op,),
                    risk_ids=op_risks,
                    surfaces_destination_id=op_surfaces,
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

            additional = LabelState()
            if getattr(result, "isError", False):
                output = {"upstream_error": True, **output}
            elif self._result_labeler is not None:
                # Raise-only: merge any per-message labels the labeler
                # derives from the output (#34). Failures never break the
                # read — labeling is best-effort enrichment on top of the
                # server's inherent-tag floor.
                try:
                    extra = self._result_labeler(upstream_name, args, output)
                except Exception:
                    extra = LabelState()
                if extra is not None and (extra.a or extra.b):
                    additional = most_restrictive_inherit(additional, extra)

            output = _maybe_truncate_output(output)
            return ToolResult(output=output, additional_tags=additional)

        return handler
