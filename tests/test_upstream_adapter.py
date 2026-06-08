"""Tests for LabeledMcpAdapter using an in-memory MCP client/server pair.

Doesn't spawn subprocesses; uses the mcp SDK's in-memory transport so
the adapter logic is tested without OS-level process management. The
manager.py subprocess path is exercised in the live demo / integration
flow rather than in CI.
"""

from __future__ import annotations

from typing import Any

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_connected_server_and_client_session

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.tools.registry import ToolContext, ToolRegistry
from capabledeputy.upstream.adapter import (
    MAX_UPSTREAM_TOOL_OUTPUT_BYTES,
    LabeledMcpAdapter,
    _infer_capability_kind,
    _maybe_truncate_output,
)
from capabledeputy.upstream.config import UpstreamServerConfig, UpstreamToolOverride


def _build_fake_server() -> Server:
    server: Server = Server("fake-upstream")

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name="read_file",
                description="Read a file from the filesystem.",
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                annotations=mcp_types.ToolAnnotations(readOnlyHint=True),
            ),
            mcp_types.Tool(
                name="write_file",
                description="Write a file.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                annotations=mcp_types.ToolAnnotations(destructiveHint=True),
            ),
            mcp_types.Tool(
                name="fetch",
                description="HTTP fetch.",
                inputSchema={"type": "object"},
            ),
        ]

    @server.call_tool()
    async def _call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> mcp_types.CallToolResult:
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text",
                    text=f"called {name} with {arguments or {}}",
                ),
            ],
            structuredContent={"name": name, "args": arguments or {}},
            isError=False,
        )

    return server


async def test_register_tools_creates_namespaced_entries() -> None:
    server = _build_fake_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(
        name="fakefs",
        command=("noop",),
        inherent_tags=LabelState(),
    )

    async with create_connected_server_and_client_session(
        server,
    ) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        names = await adapter.register_tools(registry)

    assert "fakefs.read_file" in names
    assert "fakefs.write_file" in names
    assert "fakefs.fetch" in names
    assert len(registry) == 3


async def test_disabled_tools_are_never_registered() -> None:
    """Operator hard-disable: a tool in `disabled_tools` is refused even
    when an override (or inference) would otherwise classify it. This is
    how outbound Gmail send is forbidden — it never enters the registry,
    so the planner can't propose it and no grant can enable it."""
    server = _build_fake_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(
        name="fakefs",
        command=("noop",),
        inherent_tags=LabelState(),
        # write_file would normally register (override maps it); disable it.
        tool_overrides={
            "write_file": UpstreamToolOverride(capability_kind=CapabilityKind.MODIFY_FS),
        },
        disabled_tools=frozenset({"write_file"}),
    )

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        names = await adapter.register_tools(registry)

    assert "fakefs.write_file" not in names
    assert "fakefs.write_file" not in registry
    assert "write_file" in adapter.rejected_tools
    # The other tools still register normally.
    assert "fakefs.read_file" in names


async def test_disabled_kinds_refuses_by_capability_kind() -> None:
    """disabled_kinds refuses any tool that RESOLVES to a forbidden kind,
    independent of the tool's name — the robust 'this server may not send
    email' control. Here write_file maps to MODIFY_FS via override and is
    refused because MODIFY_FS is disabled."""
    server = _build_fake_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(
        name="fakefs",
        command=("noop",),
        inherent_tags=LabelState(),
        tool_overrides={
            "write_file": UpstreamToolOverride(capability_kind=CapabilityKind.MODIFY_FS),
        },
        disabled_kinds=frozenset({"MODIFY_FS"}),
    )

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        names = await adapter.register_tools(registry)

    assert "fakefs.write_file" not in names
    assert "write_file" in adapter.rejected_tools
    assert "fakefs.read_file" in names  # other tools unaffected


async def test_gws_config_disables_outbound_send() -> None:
    """End-to-end config check: the shipped Google Workspace config
    declares the Gmail send tools as disabled, so they can never register."""
    from pathlib import Path

    from capabledeputy.upstream.config import load_config_file

    repo = Path(__file__).resolve().parents[1]
    cfg = load_config_file(repo / "configs" / "google-workspace-local.yaml")[0]
    assert "send_gmail_message" in cfg.disabled_tools
    assert "send_gmail_draft" in cfg.disabled_tools
    # Name-independent guard: no SEND_EMAIL tool can register at all.
    assert "SEND_EMAIL" in cfg.disabled_kinds


async def test_managed_gws_block_disables_send() -> None:
    """The gworkspace-setup managed block forbids SEND_EMAIL, so a re-run
    of setup can never (re-)enable outbound Gmail."""
    import yaml

    from capabledeputy.cli._managed_config import GWORKSPACE_BLOCK_BODY

    parsed = yaml.safe_load(GWORKSPACE_BLOCK_BODY)
    gws = parsed[0]
    assert "SEND_EMAIL" in gws.get("disabled_kinds", [])


async def test_inherent_labels_propagate_to_registered_tools() -> None:
    server = _build_fake_server()
    registry = ToolRegistry()
    untrusted_label_state = LabelState(
        b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})
    )
    config = UpstreamServerConfig(
        name="fetcher",
        command=("noop",),
        inherent_tags=untrusted_label_state,
    )

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)

    fetch_tool = registry.get("fetcher.fetch")
    assert ProvenanceLevel.EXTERNAL_UNTRUSTED in {t.level for t in fetch_tool.inherent_tags.b}


async def test_capability_kind_inferred_from_annotations() -> None:
    server = _build_fake_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(
        name="fakefs",
        command=("noop",),
    )

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)

    assert registry.get("fakefs.read_file").capability_kind == CapabilityKind.READ_FS
    # write_file carries destructiveHint AND a "write" name token: it must
    # map to the GRANULAR MODIFY_FS (a destructive kind) so the policy
    # engine's destructive-op gate fires. Mapping it to the legacy
    # WRITE_FS union — the pre-WI-1 behavior — silently bypassed that gate.
    assert registry.get("fakefs.write_file").capability_kind == CapabilityKind.MODIFY_FS
    assert registry.get("fakefs.fetch").capability_kind == CapabilityKind.WEB_FETCH


async def test_tool_override_supersedes_inferred_kind() -> None:
    server = _build_fake_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(
        name="fakefs",
        command=("noop",),
        tool_overrides={
            "read_file": UpstreamToolOverride(
                capability_kind=CapabilityKind.WRITE_FS,
            ),
        },
    )

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)

    assert registry.get("fakefs.read_file").capability_kind == CapabilityKind.WRITE_FS


async def test_handler_dispatches_to_upstream() -> None:
    server = _build_fake_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(name="fakefs", command=("noop",))

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)
        tool = registry.get("fakefs.read_file")

        ctx = ToolContext(session_id=__import__("uuid").uuid4(), label_state=LabelState())
        result = await tool.handler({"path": "/etc/hostname"}, ctx)

    assert result.output["name"] == "read_file"
    assert result.output["args"] == {"path": "/etc/hostname"}


def test_infer_capability_kind_email_by_name() -> None:
    assert _infer_capability_kind(None, "send_email") == CapabilityKind.SEND_EMAIL
    assert _infer_capability_kind(None, "send-mail") == CapabilityKind.SEND_EMAIL


def test_infer_capability_kind_purchase_by_name() -> None:
    assert _infer_capability_kind(None, "buy_item") == CapabilityKind.QUEUE_PURCHASE
    assert _infer_capability_kind(None, "checkout") == CapabilityKind.QUEUE_PURCHASE


def test_infer_capability_kind_unclassifiable_returns_none() -> None:
    # Pre-WI-1 this returned a permissive READ_FS default (fail open).
    # It must now return None so the caller fails closed.
    assert _infer_capability_kind(None, "do_stuff") is None


def test_infer_capability_kind_destructive_maps_to_granular() -> None:
    assert _infer_capability_kind(None, "delete_file") == CapabilityKind.DELETE_FS
    assert _infer_capability_kind(None, "update_record") == CapabilityKind.MODIFY_FS
    assert _infer_capability_kind(None, "create_doc") == CapabilityKind.CREATE_FS
    assert _infer_capability_kind(None, "delete_event") == CapabilityKind.DELETE_CAL

    destructive = mcp_types.ToolAnnotations(destructiveHint=True, readOnlyHint=False)
    assert _infer_capability_kind(destructive, "apply") == CapabilityKind.MODIFY_FS


def _build_unclassifiable_server() -> Server:
    server: Server = Server("mystery-upstream")

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name="do_stuff",
                description="Does unspecified stuff.",
                inputSchema={"type": "object"},
            ),
        ]

    @server.call_tool()
    async def _call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> mcp_types.CallToolResult:
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="ok")],
            isError=False,
        )

    return server


async def test_strict_mode_rejects_unclassifiable_tool() -> None:
    server = _build_unclassifiable_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(name="mystery", command=("noop",))  # strict=True

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        names = await adapter.register_tools(registry)

    assert names == []
    assert "do_stuff" in adapter.rejected_tools
    assert "mystery.do_stuff" not in registry


async def test_non_strict_falls_back_to_read_fs() -> None:
    server = _build_unclassifiable_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(
        name="mystery",
        command=("noop",),
        strict=False,
    )

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)

    assert adapter.rejected_tools == []
    assert registry.get("mystery.do_stuff").capability_kind == CapabilityKind.READ_FS


async def test_strict_mode_keeps_explicit_override() -> None:
    server = _build_unclassifiable_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(
        name="mystery",
        command=("noop",),
        tool_overrides={
            "do_stuff": UpstreamToolOverride(
                capability_kind=CapabilityKind.READ_FS,
            ),
        },
    )

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)

    assert registry.get("mystery.do_stuff").capability_kind == CapabilityKind.READ_FS
    assert adapter.rejected_tools == []


def test_truncate_output_small_text_passes_through() -> None:
    """Small responses are untouched — no truncation, no added flags."""
    output = {"text": "hello world"}
    result = _maybe_truncate_output(output)
    assert result == {"text": "hello world"}
    assert "truncated" not in result


def test_truncate_output_oversized_text_capped_with_marker() -> None:
    """Oversized text gets capped to the cap, and the result carries
    `truncated=True` + `original_size_bytes` so the LLM can react."""
    big = "x" * (MAX_UPSTREAM_TOOL_OUTPUT_BYTES * 3)
    output = {"text": big}
    result = _maybe_truncate_output(output)
    assert result["truncated"] is True
    assert result["original_size_bytes"] == len(big)
    # Capped head + truncation hint; total should not exceed cap +
    # a small constant for the hint message.
    assert len(result["text"].encode("utf-8")) <= MAX_UPSTREAM_TOOL_OUTPUT_BYTES + 500
    assert "truncated" in result["text"]


def test_truncate_output_preserves_other_fields() -> None:
    """Non-text fields on the output dict (e.g. upstream_error,
    structured payload keys) survive truncation."""
    big = "y" * (MAX_UPSTREAM_TOOL_OUTPUT_BYTES * 2)
    output = {"text": big, "upstream_error": True, "extra": {"k": 1}}
    result = _maybe_truncate_output(output)
    assert result["upstream_error"] is True
    assert result["extra"] == {"k": 1}
    assert result["truncated"] is True


def test_truncate_output_structured_only_no_text_passes_through() -> None:
    """When the upstream returns structuredContent without a text
    field, there's nothing to cap — pass through untouched."""
    output = {"name": "thing", "args": {"id": "abc"}}
    result = _maybe_truncate_output(output)
    assert result == output


def test_parse_config_round_trip() -> None:
    from capabledeputy.upstream.config import parse_config

    raw = {
        "upstream_servers": [
            {
                "name": "filesystem",
                "command": ["uvx", "mcp-server-filesystem", "/tmp"],
                "inherent_tags": {},
                "tool_overrides": {
                    "read_file": {"capability_kind": "READ_FS"},
                },
            },
            {
                "name": "fetch",
                "command": ["uvx", "mcp-server-fetch"],
                "inherent_tags": {"b": [{"level": "external-untrusted"}]},
            },
        ],
    }
    parsed = parse_config(raw)
    assert len(parsed) == 2
    assert parsed[0].name == "filesystem"
    assert parsed[0].command == ("uvx", "mcp-server-filesystem", "/tmp")
    assert parsed[0].tool_overrides["read_file"].capability_kind == CapabilityKind.READ_FS
    assert ProvenanceLevel.EXTERNAL_UNTRUSTED in {t.level for t in parsed[1].inherent_tags.b}
    # Fail-closed is the default posture.
    assert parsed[0].strict is True
    assert parsed[1].strict is True
