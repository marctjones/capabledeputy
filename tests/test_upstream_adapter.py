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
from capabledeputy.policy.labels import Label
from capabledeputy.tools.registry import ToolContext, ToolRegistry
from capabledeputy.upstream.adapter import LabeledMcpAdapter, _infer_capability_kind
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
        inherent_labels=frozenset(),
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


async def test_inherent_labels_propagate_to_registered_tools() -> None:
    server = _build_fake_server()
    registry = ToolRegistry()
    config = UpstreamServerConfig(
        name="fetcher",
        command=("noop",),
        inherent_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
    )

    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)

    fetch_tool = registry.get("fetcher.fetch")
    assert Label.UNTRUSTED_EXTERNAL in fetch_tool.inherent_labels


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
    assert registry.get("fakefs.write_file").capability_kind == CapabilityKind.WRITE_FS
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

        ctx = ToolContext(session_id=__import__("uuid").uuid4(), label_set=frozenset())
        result = await tool.handler({"path": "/etc/hostname"}, ctx)

    assert result.output["name"] == "read_file"
    assert result.output["args"] == {"path": "/etc/hostname"}


def test_infer_capability_kind_email_by_name() -> None:
    assert _infer_capability_kind(None, "send_email") == CapabilityKind.SEND_EMAIL
    assert _infer_capability_kind(None, "send-mail") == CapabilityKind.SEND_EMAIL


def test_infer_capability_kind_purchase_by_name() -> None:
    assert _infer_capability_kind(None, "buy_item") == CapabilityKind.QUEUE_PURCHASE
    assert _infer_capability_kind(None, "checkout") == CapabilityKind.QUEUE_PURCHASE


def test_infer_capability_kind_default_fallback() -> None:
    assert _infer_capability_kind(None, "do_stuff") == CapabilityKind.READ_FS


def test_parse_config_round_trip() -> None:
    from capabledeputy.upstream.config import parse_config

    raw = {
        "upstream_servers": [
            {
                "name": "filesystem",
                "command": ["uvx", "mcp-server-filesystem", "/tmp"],
                "inherent_labels": [],
                "tool_overrides": {
                    "read_file": {"capability_kind": "READ_FS"},
                },
            },
            {
                "name": "fetch",
                "command": ["uvx", "mcp-server-fetch"],
                "inherent_labels": ["untrusted.external"],
            },
        ],
    }
    parsed = parse_config(raw)
    assert len(parsed) == 2
    assert parsed[0].name == "filesystem"
    assert parsed[0].command == ("uvx", "mcp-server-filesystem", "/tmp")
    assert parsed[0].tool_overrides["read_file"].capability_kind == CapabilityKind.READ_FS
    assert Label.UNTRUSTED_EXTERNAL in parsed[1].inherent_labels
