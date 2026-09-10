"""MCP stdio server that proxies tool calls to a CapableDeputy daemon.

The daemon must already be running. The server connects via the daemon's
JSON-RPC socket, discovers tools via tool.list, and forwards tool calls
through tool.call. Policy denials surface as tool execution errors
(is_error=true) so the calling agent (e.g. Claude Code) sees them and
adapts in its own loop.

The `--session-id` argument binds the server to a specific CapableDeputy
session, so labels accumulate and policy decisions are made against
that session's state across the conversation.

Spec leverage (per modelcontextprotocol.io/specification/2025-11-25):

  - Real input_schema per tool (not the empty `{"type": "object"}`
    placeholder).
  - structured_content + text fallback for dict outputs, per
    "Structured Content" §.
  - is_error=true on policy denials and tool errors, per "Tool
    Execution Errors" §.
  - ToolAnnotations (read_only_hint / destructive_hint / open_world_hint)
    derived from the capability kind so MCP hosts can render
    appropriate UI confirmations per spec security guidance.
  - meta carries CapableDeputy-specific capability metadata so
    capability-aware hosts can do further filtering.
  - Resources for memory entries with labels in meta.
  - Prompts for canonical workflows.
  - Elicitation for in-flow approvals when the daemon chokepoint has
    already queued an approval object for a declassifiable action —
    the host's user confirms inline rather than running a separate
    `capdep approval approve` command.
  - Log notifications mirror policy decisions so host UIs surface
    them in real time.

Known boundary:
  - In-flow *elicitation* only appears when the daemon already returned
    an `approval_id` from the policy chokepoint. MCP never constructs
    a new approval request or grants capability to the originating
    session.
  - All denials (including the v0.7 rules capability-expired /
    rate-limit-exceeded / capability-revoked-by-prior-use) DO surface
    to the host as an is_error tool result carrying rule + reason +
    the shared deterministic recovery hint. Enforcement is unaffected
    — the daemon's `decide()` is the chokepoint; this proxy only
    relays decisions.

mcp 2.x note: request handlers are passed to `Server(...)` at
construction (`on_list_tools=`, `on_call_tool=`, ...) instead of
registered via decorators, and each handler receives an explicit
`ctx: ServerRequestContext` — there is no `server.request_context`
contextvar anymore. `_watch_capability_changes` runs as a background
task outside any request, so it can't rely on a handler's `ctx`; it
waits on `_SessionHolder`, populated from the first request any real
client makes (list_tools, during MCP initialization).
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio
import mcp.types as mcp_types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.mcp_server.media_results import build_mcp_result
from capabledeputy.presentation import DENY_RECOVERY

SERVER_NAME = "capdep"


_ANNOTATIONS_BY_KIND: dict[str, dict[str, bool]] = {
    "READ_FS": {"read_only_hint": True, "idempotent_hint": True, "open_world_hint": False},
    "WRITE_FS": {"read_only_hint": False, "destructive_hint": True, "open_world_hint": False},
    "SEND_EMAIL": {"read_only_hint": False, "destructive_hint": True, "open_world_hint": True},
    "WEB_FETCH": {"read_only_hint": True, "open_world_hint": True, "idempotent_hint": True},
    "CALENDAR_READ": {"read_only_hint": True, "idempotent_hint": True},
    "CALENDAR_WRITE": {"read_only_hint": False, "destructive_hint": True},
    "QUEUE_PURCHASE": {"read_only_hint": False, "destructive_hint": True, "open_world_hint": True},
    # Mirror the READ_FS/WRITE_FS display hints for their memory-store
    # analogs (MEMORY_READ/MEMORY_WRITE).
    "MEMORY_READ": {"read_only_hint": True, "idempotent_hint": True, "open_world_hint": False},
    "MEMORY_WRITE": {"read_only_hint": False, "destructive_hint": True, "open_world_hint": False},
}


class _SessionHolder:
    """Captures the stdio connection's single `ServerSession` the first
    time any request handler runs, so background tasks (which have no
    `ctx` of their own) can still push server-to-client notifications."""

    def __init__(self) -> None:
        self._session: ServerSession | None = None
        self._ready = anyio.Event()

    def capture(self, session: ServerSession) -> None:
        if self._session is None:
            self._session = session
            self._ready.set()

    async def wait(self) -> ServerSession:
        await self._ready.wait()
        assert self._session is not None
        return self._session


def _annotations_for(tool: dict[str, Any]) -> mcp_types.ToolAnnotations | None:
    hints = _ANNOTATIONS_BY_KIND.get(tool["capability_kind"])
    if not hints:
        return None
    return mcp_types.ToolAnnotations(
        title=tool["name"],
        **hints,
    )


def _tool_meta(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "io.capabledeputy/capability_kind": tool["capability_kind"],
    }


async def discover_tools(client: DaemonClient) -> list[mcp_types.Tool]:
    result = await client.call("tool.list")
    tools: list[mcp_types.Tool] = []
    for tool in result["tools"]:
        schema = tool.get("parameters_schema") or {"type": "object"}
        annotations = _annotations_for(tool)
        tools.append(
            mcp_types.Tool(
                name=tool["name"],
                title=tool["name"],
                description=tool["description"],
                input_schema=schema,
                output_schema=tool.get("output_schema")
                or {"type": "object", "additionalProperties": True},
                annotations=annotations,
                # mcp models alias the metadata field as `_meta`; the SDK
                # accepts the unaliased `meta` kwarg at runtime (populate_by_name)
                # but pyright's synthesized __init__ only exposes the alias.
                # Boundary ignore.
                **{"_meta": _tool_meta(tool)},  # pyright: ignore[reportArgumentType]
            ),
        )
    return tools


def _is_elicitable_denial(result: dict[str, Any]) -> bool:
    """Whether the daemon has already queued an approval to elicit."""
    if result.get("decision") != "require_approval":
        return False
    return result.get("approval_id") is not None


def _build_elicit_schema(
    tool_name: str,
    args: dict[str, Any],
    rule: str,
    approval_id: int,
) -> dict[str, Any]:
    summary = ", ".join(f"{k}={v!r}" for k, v in sorted(args.items())[:4])
    return {
        "type": "object",
        "title": f"Approval #{approval_id} needed for {tool_name}",
        "description": (
            f"This action was blocked by the policy rule '{rule}'. "
            "If you confirm, capdep will approve the daemon-queued request "
            "and execute it through the existing purpose-limited approval "
            "path; the originating session will not gain the denied capability."
        ),
        "properties": {
            "approve": {
                "type": "boolean",
                "title": f"Approve queued request #{approval_id}?",
                "description": (
                    f"Tool: {tool_name}. "
                    f"Arguments: {summary or '(none)'}. "
                    "Approve only if this matches the user's intent."
                ),
            },
        },
        "required": ["approve"],
    }


async def _try_elicit_and_approve(
    client: DaemonClient,
    session: ServerSession,
    tool_name: str,
    args: dict[str, Any],
    deny_result: dict[str, Any],
) -> mcp_types.CallToolResult | None:
    rule = deny_result.get("rule") or ""
    approval_id = deny_result.get("approval_id")
    if approval_id is None:
        return None
    schema = _build_elicit_schema(tool_name, args, rule, int(approval_id))
    try:
        elicit_result = await session.elicit(
            message=(
                f"Approve queued capdep request #{approval_id} for {tool_name} "
                f"despite policy rule '{rule}'?"
            ),
            requested_schema={
                "type": "object",
                "properties": schema["properties"],
                "required": schema["required"],
            },
        )
    except Exception:
        return None

    if elicit_result.action != "accept":
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text",
                    text=(
                        f"User declined elicitation for {tool_name} "
                        f"(action={elicit_result.action})."
                    ),
                ),
            ],
            is_error=True,
        )

    content = elicit_result.content or {}
    if not content.get("approve"):
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text",
                    text="User did not approve via elicitation.",
                ),
            ],
            is_error=True,
        )

    approved = await client.call(
        "approval.approve",
        {"id": int(approval_id), "decided_by": "mcp-elicitation"},
    )
    dispatch = approved.get("dispatch") or {}
    if dispatch.get("decision") == "allow":
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text",
                    text=(
                        "Approved queued request via elicitation; executed in purpose-limited "
                        f"session {approved.get('executed_in_session')}.\n\n"
                        f"{json.dumps(dispatch.get('output') or {}, indent=2)}"
                    ),
                ),
            ],
            is_error=False,
        )
    return mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(
                type="text",
                text=(
                    "Elicitation accepted but execution failed: "
                    f"{dispatch.get('reason') or dispatch.get('error') or 'unknown'}"
                ),
            ),
        ],
        is_error=True,
    )


async def _send_log(session: ServerSession, level: str, message: str) -> None:
    with suppress(Exception):
        await session.send_log_message(
            level=level,  # type: ignore[arg-type]
            data=message,
            logger="capdep",
        )


async def dispatch_tool(
    client: DaemonClient,
    session_id: UUID,
    name: str,
    arguments: dict[str, Any],
    session: ServerSession | None = None,
) -> mcp_types.CallToolResult:
    result = await client.call(
        "tool.call",
        {
            "session_id": str(session_id),
            "tool": name,
            "args": arguments,
        },
    )

    if result.get("error"):
        text = f"tool error: {result['error']}"
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            is_error=True,
        )

    if result["decision"] != "allow":
        if session is not None and _is_elicitable_denial(result):
            await _send_log(
                session,
                "warning",
                "policy requires approval for "
                f"{name} (approval_id={result.get('approval_id')}, rule={result.get('rule')}); "
                "offering elicitation",
            )
            elicit_result = await _try_elicit_and_approve(
                client,
                session,
                name,
                arguments,
                result,
            )
            if elicit_result is not None:
                return elicit_result

        rule = result.get("rule") or "no_rule"
        reason = result.get("reason") or ""
        text = f"policy denied (decision={result['decision']}, rule={rule}): {reason}"
        # Surface the same deterministic operator recovery the REPL /
        # TUI / console show (shared, tested presentation.DENY_RECOVERY)
        # so MCP hosts get actionable guidance for the v0.7 rules
        # (capability-expired / rate-limit-exceeded / revoked) too —
        # not just an opaque denial.
        recovery = DENY_RECOVERY.get(rule)
        if recovery:
            text += f"  [recover: {recovery}]"
        meta: dict[str, Any] = {
            "io.capabledeputy/decision": result["decision"],
            "io.capabledeputy/rule": rule,
            "io.capabledeputy/effective_labels": result.get("effective_labels", []),
            "io.capabledeputy/approval_id": result.get("approval_id"),
        }
        if session is not None:
            await _send_log(
                session,
                "warning",
                f"policy denied {name}: rule={rule}",
            )
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            is_error=True,
            **{"_meta": meta},  # pyright: ignore[reportArgumentType]
        )

    media_payload: dict[str, Any] = dict(result)
    if result.get("labels_added"):
        media_payload = {
            **media_payload,
            "labels_added_note": (
                "[capdep: session labels expanded with " + ", ".join(result["labels_added"]) + "]"
            ),
        }

    call_meta: dict[str, Any] = {
        "io.capabledeputy/labels_added": result.get("labels_added", []),
    }

    if session is not None and result.get("labels_added"):
        await _send_log(
            session,
            "info",
            f"tool {name} succeeded; labels expanded with " + ", ".join(result["labels_added"]),
        )

    built = build_mcp_result(media_payload, meta=call_meta, is_error=False)
    if result.get("labels_added") and built.content:
        first = built.content[0]
        if isinstance(first, mcp_types.TextContent):
            first.text += (
                "\n\n[capdep: session labels expanded with "
                + ", ".join(result["labels_added"])
                + "]"
            )
    return built


async def build_server(
    client: DaemonClient,
    session_id: UUID,
) -> tuple[Server, _SessionHolder]:
    from capabledeputy.mcp_server import prompts as _prompts
    from capabledeputy.mcp_server import resources as _resources

    session_holder = _SessionHolder()

    async def _on_list_tools(
        ctx: ServerRequestContext,
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        session_holder.capture(ctx.session)
        return mcp_types.ListToolsResult(tools=await discover_tools(client))

    async def _on_call_tool(
        ctx: ServerRequestContext,
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        session_holder.capture(ctx.session)
        return await dispatch_tool(
            client,
            session_id,
            params.name,
            params.arguments or {},
            ctx.session,
        )

    async def _on_list_resources(
        ctx: ServerRequestContext,
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListResourcesResult:
        session_holder.capture(ctx.session)
        return mcp_types.ListResourcesResult(resources=await _resources.list_resources(client))

    async def _on_read_resource(
        ctx: ServerRequestContext,
        params: mcp_types.ReadResourceRequestParams,
    ) -> mcp_types.ReadResourceResult:
        session_holder.capture(ctx.session)
        text = await _resources.read_resource(client, session_id, str(params.uri))
        return mcp_types.ReadResourceResult(
            contents=[
                mcp_types.TextResourceContents(
                    uri=str(params.uri),
                    mime_type="application/json",
                    text=text,
                ),
            ],
        )

    async def _on_list_prompts(
        ctx: ServerRequestContext,
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListPromptsResult:
        session_holder.capture(ctx.session)
        return mcp_types.ListPromptsResult(prompts=_prompts.list_prompts())

    async def _on_get_prompt(
        ctx: ServerRequestContext,
        params: mcp_types.GetPromptRequestParams,
    ) -> mcp_types.GetPromptResult:
        session_holder.capture(ctx.session)
        return _prompts.get_prompt(params.name, params.arguments)

    server: Server = Server(
        SERVER_NAME,
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
        on_list_resources=_on_list_resources,
        on_read_resource=_on_read_resource,
        on_list_prompts=_on_list_prompts,
        on_get_prompt=_on_get_prompt,
    )
    return server, session_holder


async def _watch_capability_changes(
    socket_path: Path,
    session_id: UUID,
    session_holder: _SessionHolder,
) -> None:
    """Subscribe to the daemon's audit stream and emit MCP
    tools/list_changed when our bound session's capabilities change."""
    # Untrusted: agent-facing, same boundary as serve() below.
    client = DaemonClient(socket_path, trusted=False)
    target = str(session_id)
    with suppress(Exception):
        async for event in await client.subscribe(["audit"]):
            data = event.get("data") or {}
            if data.get("event_type") != "capability.granted":
                continue
            if (data.get("session_id") or "") != target:
                continue
            with suppress(Exception):
                session = await session_holder.wait()
                await session.send_tool_list_changed()


async def serve(session_id: UUID, socket_path: Path | None = None) -> None:
    import anyio as _anyio

    socket = socket_path or default_socket_path()
    # Untrusted: this is the session-bound MCP server external agent hosts
    # (Claude Code, Codex, etc.) connect to. It must never carry operator
    # authority — see daemon/authz.py.
    client = DaemonClient(socket, trusted=False)
    server, session_holder = await build_server(client, session_id)
    async with (
        stdio_server() as (read_stream, write_stream),
        _anyio.create_task_group() as tg,
    ):
        tg.start_soon(_watch_capability_changes, socket, session_id, session_holder)
        try:
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
        finally:
            tg.cancel_scope.cancel()
