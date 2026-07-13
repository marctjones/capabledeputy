"""MCP stdio server that proxies tool calls to a CapableDeputy daemon.

The daemon must already be running. The server connects via the daemon's
JSON-RPC socket, discovers tools via tool.list, and forwards tool calls
through tool.call. Policy denials surface as tool execution errors
(isError=true) so the calling agent (e.g. Claude Code) sees them and
adapts in its own loop.

The `--session-id` argument binds the server to a specific CapableDeputy
session, so labels accumulate and policy decisions are made against
that session's state across the conversation.

Spec leverage (per modelcontextprotocol.io/specification/2025-11-25):

  - Real inputSchema per tool (not the empty `{"type": "object"}`
    placeholder).
  - structuredContent + text fallback for dict outputs, per
    "Structured Content" §.
  - isError=true on policy denials and tool errors, per "Tool
    Execution Errors" §.
  - ToolAnnotations (readOnlyHint / destructiveHint / openWorldHint)
    derived from the capability kind so MCP hosts can render
    appropriate UI confirmations per spec security guidance.
  - _meta carries CapableDeputy-specific capability metadata so
    capability-aware hosts can do further filtering.
  - Resources for memory entries with labels in _meta.
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
    to the host as an isError tool result carrying rule + reason +
    the shared deterministic recovery hint. Enforcement is unaffected
    — the daemon's `decide()` is the chokepoint; this proxy only
    relays decisions.
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.mcp_server.media_results import build_mcp_result
from capabledeputy.presentation import DENY_RECOVERY

SERVER_NAME = "capdep"


_ANNOTATIONS_BY_KIND: dict[str, dict[str, bool]] = {
    "READ_FS": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "WRITE_FS": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    "SEND_EMAIL": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
    "WEB_FETCH": {"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
    "CALENDAR_READ": {"readOnlyHint": True, "idempotentHint": True},
    "CALENDAR_WRITE": {"readOnlyHint": False, "destructiveHint": True},
    "QUEUE_PURCHASE": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
}


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
                inputSchema=schema,
                outputSchema=tool.get("output_schema")
                or {"type": "object", "additionalProperties": True},
                annotations=annotations,
                # mcp `_meta` alias; SDK accepts at runtime, pyright's
                # generated model doesn't expose it. Boundary ignore.
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
    server: Server,
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
        elicit_result = await server.request_context.session.elicit(
            message=(
                f"Approve queued capdep request #{approval_id} for {tool_name} "
                f"despite policy rule '{rule}'?"
            ),
            # mcp's elicit() takes requestedSchema as a plain dict
            # (camelCase). The prior code passed `requested_schema=`
            # with an ElicitRequestedSchema object — wrong kwarg AND
            # wrong type; this path would have raised at runtime when
            # the email-approval elicitation fired.
            requestedSchema={
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
            isError=True,
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
            isError=True,
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
            isError=False,
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
        isError=True,
    )


async def _send_log(server: Server, level: str, message: str) -> None:
    with suppress(Exception):
        await server.request_context.session.send_log_message(
            level=level,  # type: ignore[arg-type]
            data=message,
            logger="capdep",
        )


async def dispatch_tool(
    client: DaemonClient,
    session_id: UUID,
    name: str,
    arguments: dict[str, Any],
    server: Server | None = None,
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
            isError=True,
        )

    if result["decision"] != "allow":
        if server is not None and _is_elicitable_denial(result):
            await _send_log(
                server,
                "warning",
                "policy requires approval for "
                f"{name} (approval_id={result.get('approval_id')}, rule={result.get('rule')}); "
                "offering elicitation",
            )
            elicit_result = await _try_elicit_and_approve(
                client,
                server,
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
        if server is not None:
            await _send_log(
                server,
                "warning",
                f"policy denied {name}: rule={rule}",
            )
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            isError=True,
            **{"_meta": meta},
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

    if server is not None and result.get("labels_added"):
        await _send_log(
            server,
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


async def build_server(client: DaemonClient, session_id: UUID) -> Server:
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return await discover_tools(client)

    @server.call_tool()
    async def _call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> mcp_types.CallToolResult:
        return await dispatch_tool(client, session_id, name, arguments or {}, server)

    from capabledeputy.mcp_server import prompts as _prompts
    from capabledeputy.mcp_server import resources as _resources

    @server.list_resources()
    async def _list_resources() -> list[mcp_types.Resource]:
        return await _resources.list_resources(client)

    @server.read_resource()
    async def _read_resource(uri: Any) -> str:
        return await _resources.read_resource(client, session_id, str(uri))

    @server.list_prompts()
    async def _list_prompts() -> list[mcp_types.Prompt]:
        return _prompts.list_prompts()

    @server.get_prompt()
    async def _get_prompt(
        name: str,
        arguments: dict[str, str] | None,
    ) -> mcp_types.GetPromptResult:
        return _prompts.get_prompt(name, arguments)

    return server


async def _watch_capability_changes(
    socket_path: Path,
    session_id: UUID,
    server: Server,
) -> None:
    """Subscribe to the daemon's audit stream and emit MCP
    tools/list_changed when our bound session's capabilities change."""
    client = DaemonClient(socket_path)
    target = str(session_id)
    with suppress(Exception):
        async for event in await client.subscribe(["audit"]):
            data = event.get("data") or {}
            if data.get("event_type") != "capability.granted":
                continue
            if (data.get("session_id") or "") != target:
                continue
            with suppress(Exception):
                await server.request_context.session.send_tool_list_changed()


async def serve(session_id: UUID, socket_path: Path | None = None) -> None:
    import anyio as _anyio

    socket = socket_path or default_socket_path()
    client = DaemonClient(socket)
    server = await build_server(client, session_id)
    async with (
        stdio_server() as (read_stream, write_stream),
        _anyio.create_task_group() as tg,
    ):
        tg.start_soon(_watch_capability_changes, socket, session_id, server)
        try:
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
        finally:
            tg.cancel_scope.cancel()
