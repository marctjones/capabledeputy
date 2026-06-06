"""Live read-only tests against the user's real Google Workspace.

OPT-IN: set CAPDEP_GWS_LIVE=1 to run. Skipped by default.

PRE-REQS:
  - `gws` binary on PATH (`npm install -g @googleworkspace/cli`)
  - `gws auth login -s drive,gmail,calendar,docs,sheets` already run
    on this machine — refresh tokens cached in the OS keyring.

NON-DESTRUCTIVE BY CONSTRUCTION: this suite only invokes tools whose
names match read-only patterns. The allowlist is checked twice:
  1. We grep the registered tool list for safe names before calling.
  2. The chokepoint gets ONLY READ_FS / CALENDAR_READ caps for the
     test session — anything destructive would be DENIED.

Tools allowed: anything ending in `.list`, `.get`, `.search` for
gmail / drive / calendar / docs / sheets. Tools containing
`send`/`delete`/`update`/`create`/`insert`/`patch`/`archive`/`trash`
are explicitly blocked even if the chokepoint would allow them.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.adapter import LabeledMcpAdapter
from capabledeputy.upstream.config import UpstreamServerConfig
from capabledeputy.upstream.supervisor import LiveSession

GWS_LIVE_OPT_IN = pytest.mark.skipif(
    not os.environ.get("CAPDEP_GWS_LIVE"),
    reason="CAPDEP_GWS_LIVE not set",
)

GWS_BIN = pytest.mark.skipif(
    shutil.which("gws") is None,
    reason="gws binary not on PATH",
)


# Names with any of these substrings are REFUSED by this test suite
# even if the agent's capability set would let them through. Belt +
# suspenders against destructive ops slipping in.
_DESTRUCTIVE_SUBSTRINGS: tuple[str, ...] = (
    "send",
    "delete",
    "update",
    "create",
    "insert",
    "patch",
    "archive",
    "trash",
    "remove",
    "modify",
    "write",
    "move",
    "copy",
)


def _is_read_only(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return not any(d in lowered for d in _DESTRUCTIVE_SUBSTRINGS)


def _cap_read_only() -> frozenset[Capability]:
    """Only READ caps — no WRITE/CREATE/MODIFY/DELETE/SEND. Even if a
    test slipped a destructive tool through, the chokepoint would
    DENY it for lack of cap match."""
    return frozenset(
        {
            Capability(kind=CapabilityKind.READ_FS, pattern="*"),
            Capability(kind=CapabilityKind.CALENDAR_READ, pattern="*"),
        },
    )


@pytest.fixture
def writer(tmp_path: Path) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


@pytest.fixture
def graph(writer: AuditWriter) -> SessionGraph:
    return SessionGraph(audit=writer)


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def gws_config() -> UpstreamServerConfig:
    """Spawn config for `gws-mcp-server` — the community MCP wrapper
    around the gws Workspace CLI. Mirrors the managed block's overrides
    so the live test exercises what production actually does. Without
    the explicit gmail_* overrides the adapter's name-based inference
    matches "gmail" → SEND_EMAIL, which would incorrectly tag a list
    call as outbound."""
    from capabledeputy.policy.labels import CategoryTag, LabelState
    from capabledeputy.policy.tiers import Tier
    from capabledeputy.upstream.config import UpstreamToolOverride

    read_personal = UpstreamToolOverride(
        capability_kind=CapabilityKind.READ_FS,
        additional_tags=LabelState(
            a={CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")},
        ),
    )
    overrides = {
        "gmail_messages_list": read_personal,
        "gmail_messages_get": read_personal,
        "gmail_threads_list": read_personal,
        "gmail_threads_get": read_personal,
    }
    return UpstreamServerConfig(
        name="gws",
        command=("gws-mcp-server", "--services", "drive,sheets,calendar,docs,gmail"),
        inherent_tags=LabelState(),
        tool_overrides=overrides,
        isolation=None,
        env={},
        strict=False,
    )


# ---- Live tests -----------------------------------------------------------


@GWS_LIVE_OPT_IN
@GWS_BIN
async def test_gws_spawns_and_registers_tools(
    gws_config: UpstreamServerConfig,
    registry: ToolRegistry,
) -> None:
    """The fundamental smoke: `gws mcp` spawns, MCP handshake
    completes, the adapter discovers tools, and registration
    succeeds for at least one read-only call."""
    live = LiveSession(gws_config)
    await live.start()
    try:
        adapter = LabeledMcpAdapter(config=gws_config, session=live)
        registered = await adapter.register_tools(registry)
        assert registered, "gws mcp registered no tools"
        # At least one of the canonical read-only patterns must be
        # present — gmail / drive / calendar all support list.
        read_only = [n for n in registered if _is_read_only(n)]
        assert read_only, f"no read-only gws tools registered out of {len(registered)} total"
    finally:
        await live.stop()


@GWS_LIVE_OPT_IN
@GWS_BIN
async def test_gmail_list_threads_round_trip(
    gws_config: UpstreamServerConfig,
    graph: SessionGraph,
    registry: ToolRegistry,
    writer: AuditWriter,
) -> None:
    """Read 3 most recent Gmail threads via the chokepoint. Verifies:
      - gws mcp spawns successfully
      - the gmail thread-list tool registers
      - the chokepoint ALLOWS the read with READ_FS cap
      - real Gmail content comes back
    No send / no delete / no modify."""
    live = LiveSession(gws_config)
    await live.start()
    try:
        adapter = LabeledMcpAdapter(config=gws_config, session=live)
        registered = await adapter.register_tools(registry)

        # Find a gmail-thread-list tool from whatever names gws picked.
        # We don't pin the exact form because gws may evolve naming;
        # the substring check is robust to that.
        candidates = [
            n
            for n in registered
            if "gmail" in n.lower()
            and "thread" in n.lower()
            and ("list" in n.lower() or "search" in n.lower())
        ]
        if not candidates:
            pytest.skip(
                f"no gmail thread-list-style tool found in {registered[:10]}; "
                "tool naming may have changed",
            )
        tool_name = candidates[0]
        assert _is_read_only(tool_name), f"sanity: {tool_name} not read-only?"

        # Build a session with read-only caps.
        s = await graph.new(intent="gmail readonly smoke")
        graph._sessions[s.id] = replace(
            s,
            capability_set=_cap_read_only(),
        )
        client = LabeledToolClient(registry, graph, writer)

        outcome = await client.call_tool(
            s.id,
            tool_name,
            {"maxResults": 3},
        )
        assert outcome.decision.value == "allow", f"{tool_name} denied: {outcome.reason}"
        # The call returned SOMETHING. Don't pin shape — gmail's
        # response varies.
        assert outcome.output is not None
    finally:
        await live.stop()


@GWS_LIVE_OPT_IN
@GWS_BIN
async def test_drive_list_files_round_trip(
    gws_config: UpstreamServerConfig,
    graph: SessionGraph,
    registry: ToolRegistry,
    writer: AuditWriter,
) -> None:
    """Read up to 3 Drive files via the chokepoint."""
    live = LiveSession(gws_config)
    await live.start()
    try:
        adapter = LabeledMcpAdapter(config=gws_config, session=live)
        registered = await adapter.register_tools(registry)

        candidates = [
            n
            for n in registered
            if "drive" in n.lower() and ("list" in n.lower() or "files" in n.lower())
        ]
        if not candidates:
            pytest.skip(f"no drive list-style tool found in {registered[:10]}")
        tool_name = candidates[0]
        assert _is_read_only(tool_name)

        s = await graph.new(intent="drive readonly smoke")
        graph._sessions[s.id] = replace(
            s,
            capability_set=_cap_read_only(),
        )
        client = LabeledToolClient(registry, graph, writer)
        outcome = await client.call_tool(
            s.id,
            tool_name,
            {"pageSize": 3},
        )
        assert outcome.decision.value == "allow", f"{tool_name} denied: {outcome.reason}"
        assert outcome.output is not None
    finally:
        await live.stop()


@GWS_LIVE_OPT_IN
@GWS_BIN
async def test_calendar_list_events_round_trip(
    gws_config: UpstreamServerConfig,
    graph: SessionGraph,
    registry: ToolRegistry,
    writer: AuditWriter,
) -> None:
    """Read upcoming calendar events via the chokepoint."""
    live = LiveSession(gws_config)
    await live.start()
    try:
        adapter = LabeledMcpAdapter(config=gws_config, session=live)
        registered = await adapter.register_tools(registry)

        candidates = [
            n
            for n in registered
            if "calendar" in n.lower() and "list" in n.lower() and "event" in n.lower()
        ]
        if not candidates:
            pytest.skip(
                f"no calendar list-events tool found in {registered[:10]}",
            )
        tool_name = candidates[0]
        assert _is_read_only(tool_name)

        s = await graph.new(intent="calendar readonly smoke")
        graph._sessions[s.id] = replace(
            s,
            capability_set=_cap_read_only(),
        )
        client = LabeledToolClient(registry, graph, writer)
        # The calendar list-events tool typically wants a calendarId
        # (`primary` is the user's own calendar) and a maxResults cap.
        outcome = await client.call_tool(
            s.id,
            tool_name,
            {"calendarId": "primary", "maxResults": 3},
        )
        assert outcome.decision.value == "allow", f"{tool_name} denied: {outcome.reason}"
        assert outcome.output is not None
    finally:
        await live.stop()


@GWS_LIVE_OPT_IN
@GWS_BIN
async def test_destructive_tools_are_denied(
    gws_config: UpstreamServerConfig,
    graph: SessionGraph,
    registry: ToolRegistry,
    writer: AuditWriter,
) -> None:
    """Belt-and-suspenders: explicitly attempt a SEND tool with a
    read-only-capped session and assert the chokepoint DENIES it.
    No actual email is sent (the call never reaches the upstream)."""
    live = LiveSession(gws_config)
    await live.start()
    try:
        adapter = LabeledMcpAdapter(config=gws_config, session=live)
        registered = await adapter.register_tools(registry)

        # Find a send-style tool.
        send_candidates = [n for n in registered if "gmail" in n.lower() and "send" in n.lower()]
        if not send_candidates:
            pytest.skip("no gmail send-style tool registered to test")
        tool_name = send_candidates[0]

        s = await graph.new(intent="destructive-denial check")
        graph._sessions[s.id] = replace(
            s,
            capability_set=_cap_read_only(),
        )
        client = LabeledToolClient(registry, graph, writer)

        outcome = await client.call_tool(
            s.id,
            tool_name,
            {
                "to": "nobody@example.invalid",
                "subject": "this must not be sent",
                "body": "denial test",
            },
        )
        # MUST be denied — read-only caps don't include SEND_EMAIL.
        assert outcome.decision.value != "allow", (
            f"DESTRUCTIVE TOOL {tool_name} was NOT denied — the policy "
            "engine let a send through with read-only caps. Investigate "
            "immediately."
        )
    finally:
        await live.stop()
