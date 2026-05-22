"""Workflow smoke tests: exercise every bundled MCP server's tools
through the FULL chokepoint (LabeledMcpAdapter → ToolRegistry →
LabeledToolClient.call_tool → real policy decisions) using an
in-memory MCP transport.

NON-DESTRUCTIVE BY CONSTRUCTION:
  - Local fs: read/list/create/write only on a per-test tempdir.
    fs.delete is intentionally NOT exercised — the user asked for
    no destructive ops anywhere in the suite.
  - Memory: create/read/list/update on an in-process memory store.
    memory.delete is also skipped.
  - Git: all calls are read-only (status, log, branch_list).
  - Fetch / search: smoke-only (registration); no live HTTP.
  - Imap: covered by test_imap_server.py + the live-readonly suite
    that opt-ins via CAPDEP_GWS_LIVE.

This is path-1 (daemon RPC scripting) of the three-way test plan.
Path 2 is the pexpect REPL driver; path 3 is the gws-readonly live
test (CAPDEP_GWS_LIVE=1).
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.mcp_servers import fs as fs_server
from capabledeputy.mcp_servers import git as git_server
from capabledeputy.mcp_servers import memory as memory_server
from capabledeputy.mcp_servers._common import build_server
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
)
from capabledeputy.policy.labels import Label
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.adapter import LabeledMcpAdapter
from capabledeputy.upstream.config import UpstreamServerConfig


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def writer(tmp_path: Path) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


@pytest.fixture
def graph(writer: AuditWriter) -> SessionGraph:
    return SessionGraph(audit=writer)


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


def _cap(
    kind: CapabilityKind,
    *,
    pattern: str = "*",
    allows_destructive: bool = False,
) -> Capability:
    return Capability(
        kind=kind,
        pattern=pattern,
        allows_destructive=allows_destructive,
    )


async def _make_session_with_caps(
    graph: SessionGraph,
    caps: frozenset[Capability],
    *,
    intent: str = "workflow",
):
    """Create a session and stuff the cap set directly. This is the
    pattern used by test_approval_route.py — bypasses
    grant_capability's purpose-admissibility check that's irrelevant
    for these workflow smoke tests."""
    s = await graph.new(intent=intent)
    graph._sessions[s.id] = replace(s, capability_set=caps)
    return graph._sessions[s.id]


def _build_upstream_config(server_name: str) -> UpstreamServerConfig:
    """Minimal UpstreamServerConfig sufficient for the adapter; the
    command/env fields are unused because we wire via the in-memory
    MCP transport, not a subprocess."""
    return UpstreamServerConfig(
        name=server_name,
        command=("never", "spawned"),
        inherent_labels=frozenset(),
        tool_overrides={},
        isolation=None,
        env={},
        strict=False,
    )


# --- Filesystem (fs) -------------------------------------------------------


async def test_fs_read_list_create_write_workflow(
    graph: SessionGraph,
    registry: ToolRegistry,
    writer: AuditWriter,
    tmp_path: Path,
) -> None:
    """End-to-end: create a file via fs.create, read it back, list the
    directory, overwrite via fs.write. Each call goes through the
    chokepoint with a session that holds the matching cap."""
    server = build_server("fs", fs_server.tools())
    config = _build_upstream_config("fs")
    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)

        session_state = await _make_session_with_caps(
            graph,
            frozenset({
                _cap(CapabilityKind.READ_FS),
                _cap(CapabilityKind.CREATE_FS),
                _cap(
                    CapabilityKind.WRITE_FS,
                    allows_destructive=True,
                ),
            }),
            intent="fs workflow",
        )
        client = LabeledToolClient(registry, graph, writer)

        target = tmp_path / "hello.txt"

        # 1. fs.create — note the tool is namespaced under the upstream
        # name from the config: "fs.fs.create" (server "fs" + tool
        # "fs.create"). The adapter prepends `config.name + "."`.
        out = await client.call_tool(
            session_state.id,
            "fs.fs.create",
            {"path": str(target), "content": "hello"},
        )
        assert out.decision.value == "allow", f"fs.create denied: {out.reason}"
        assert target.is_file()
        assert target.read_text() == "hello"

        # 2. fs.read
        out = await client.call_tool(
            session_state.id,
            "fs.fs.read",
            {"path": str(target)},
        )
        assert out.decision.value == "allow"
        assert "hello" in str(out.output)

        # 3. fs.list
        out = await client.call_tool(
            session_state.id,
            "fs.fs.list",
            {"path": str(tmp_path)},
        )
        assert out.decision.value == "allow"
        assert "hello.txt" in str(out.output)

        # 4. fs.write — overwrite. Destructive cap above lets it pass.
        out = await client.call_tool(
            session_state.id,
            "fs.fs.write",
            {"path": str(target), "content": "updated"},
        )
        assert out.decision.value == "allow", f"fs.write denied: {out.reason}"
        assert target.read_text() == "updated"

        # fs.delete is NOT exercised (no destructive ops in this suite).


# --- Memory ----------------------------------------------------------------


async def test_memory_create_read_list_update_workflow(
    graph: SessionGraph,
    registry: ToolRegistry,
    writer: AuditWriter,
) -> None:
    """Memory server: create → read → list → update (no delete)."""
    server = build_server("memory", memory_server.tools())
    config = _build_upstream_config("memory")
    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)

        session_state = await _make_session_with_caps(
            graph,
            frozenset({
                _cap(CapabilityKind.READ_FS),
                _cap(CapabilityKind.CREATE_FS),
                _cap(
                    CapabilityKind.WRITE_FS,
                    allows_destructive=True,
                ),
            }),
            intent="memory workflow",
        )
        client = LabeledToolClient(registry, graph, writer)

        out = await client.call_tool(
            session_state.id,
            "memory.memory.create",
            {"key": "weekly_goals", "value": "ship sandbox"},
        )
        assert out.decision.value == "allow", f"memory.create denied: {out.reason}"

        out = await client.call_tool(
            session_state.id,
            "memory.memory.list",
            {},
        )
        assert out.decision.value == "allow"
        assert "weekly_goals" in str(out.output)

        out = await client.call_tool(
            session_state.id,
            "memory.memory.read",
            {"key": "weekly_goals"},
        )
        assert out.decision.value == "allow"
        assert "ship sandbox" in str(out.output)

        out = await client.call_tool(
            session_state.id,
            "memory.memory.update",
            {"key": "weekly_goals", "value": "ship sandbox + tests"},
        )
        assert out.decision.value == "allow", f"memory.update denied: {out.reason}"

        # memory.delete intentionally not exercised.


# --- Git (read-only) -------------------------------------------------------


async def test_git_read_only_workflow(
    graph: SessionGraph,
    registry: ToolRegistry,
    writer: AuditWriter,
    tmp_path: Path,
) -> None:
    """Spin up a tiny git repo in tmp and exercise read-only git.*
    tools through the chokepoint."""
    # Make a tiny repo with a single commit.
    subprocess.run(  # noqa: S603
        ["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
    )
    (tmp_path / "x.txt").write_text("hi\n")
    subprocess.run(["git", "add", "x.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=tmp_path, check=True,
    )

    server = build_server("git", git_server.tools())
    config = _build_upstream_config("git")
    async with create_connected_server_and_client_session(server) as session:
        adapter = LabeledMcpAdapter(config=config, session=session)
        await adapter.register_tools(registry)

        session_state = await _make_session_with_caps(
            graph,
            frozenset({_cap(CapabilityKind.READ_FS)}),
            intent="git read",
        )
        client = LabeledToolClient(registry, graph, writer)

        for tool_name, args in [
            ("git.git.status", {"path": str(tmp_path)}),
            ("git.git.log", {"path": str(tmp_path)}),
            ("git.git.branch_list", {"path": str(tmp_path)}),
        ]:
            out = await client.call_tool(
                session_state.id,
                tool_name,
                args,
            )
            assert out.decision.value == "allow", (
                f"{tool_name} denied: {out.reason}"
            )


# --- Registration smoke for fetch / search ---------------------------------


async def test_fetch_and_search_register(registry: ToolRegistry) -> None:
    """Fetch + search need network/keys to exercise their GET paths.
    The smoke here is registration only: they register, the inherent
    label `untrusted.external` is attached, and the adapter wires the
    handlers."""
    from capabledeputy.mcp_servers import fetch as fetch_server
    from capabledeputy.mcp_servers import search as search_server

    for module, server_name, expected_label in (
        (fetch_server, "fetch", Label.UNTRUSTED_EXTERNAL),
        (search_server, "search", Label.UNTRUSTED_EXTERNAL),
    ):
        server = build_server(server_name, module.tools())
        config = UpstreamServerConfig(
            name=server_name,
            command=("never", "spawned"),
            inherent_labels=frozenset({expected_label}),
            tool_overrides={},
            isolation=None,
            env={},
            strict=False,
        )
        async with create_connected_server_and_client_session(server) as session:
            adapter = LabeledMcpAdapter(config=config, session=session)
            registered = await adapter.register_tools(registry)
            assert registered, f"{server_name} registered no tools"
            # The first tool in the registry from this server must carry
            # the inherent untrusted.external label so its outputs taint
            # sessions that call it.
            first_tool_name = registered[0]
            first_tool = registry.get(first_tool_name)
            assert expected_label in first_tool.inherent_labels, (
                f"{first_tool_name} missing {expected_label} label"
            )
