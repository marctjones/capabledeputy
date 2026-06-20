"""Tests for the MCP server. Drives a real daemon to exercise the proxy
path end-to-end without speaking the MCP wire protocol — that's the
SDK's job. We test our discover_tools and dispatch_tool functions
directly since they hold the proxy logic.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import anyio
import mcp.types as mcp_types
import pytest

from capabledeputy.app import App
from capabledeputy.daemon.audit_handlers import make_audit_handlers
from capabledeputy.daemon.handlers import default_handlers
from capabledeputy.daemon.policy_handlers import make_policy_handlers
from capabledeputy.daemon.server import Daemon
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.daemon.tool_handlers import make_tool_handlers
from capabledeputy.ipc.client import DaemonClient
from capabledeputy.mcp_server.server import discover_tools, dispatch_tool
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier
from tests._socket_helpers import short_socket_path


def _text(result: object) -> str:
    """First content item as text. Asserts the union member is
    TextContent (pyright can't narrow the content union otherwise);
    also strengthens the test — content[0] really is text."""
    c = result.content[0]  # type: ignore[attr-defined]
    assert isinstance(c, mcp_types.TextContent)
    return c.text


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "socket": short_socket_path(),
        "state_db": tmp_path / "state.db",
        "audit_log": tmp_path / "audit.jsonl",
    }


async def _build_daemon(paths: dict[str, Path]) -> tuple[Daemon, App]:
    app = App(
        state_db_path=paths["state_db"],
        audit_log_path=paths["audit_log"],
    )
    await app.startup()
    handlers = default_handlers()
    handlers.update(make_session_handlers(app.graph))
    handlers.update(make_audit_handlers(app.audit))
    handlers.update(make_policy_handlers())
    handlers.update(make_tool_handlers(app.registry, app.graph, app.tool_client))
    return Daemon(paths["socket"], handlers=handlers), app


async def _wait_for_socket(path: Path, timeout: float = 2.0) -> None:
    deadline = anyio.current_time() + timeout
    while anyio.current_time() < deadline:
        if path.exists():
            try:
                stream = await anyio.connect_unix(str(path))
                await stream.aclose()
                return
            except (FileNotFoundError, ConnectionRefusedError):
                pass
        await anyio.sleep(0.01)
    raise TimeoutError(f"socket {path} did not become available within {timeout}s")


async def test_discover_tools_finds_native_tools(paths: dict[str, Path]) -> None:
    daemon, _app = await _build_daemon(paths)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        tools = await discover_tools(client)
        names = {t.name for t in tools}
        assert "memory.read" in names
        assert "memory.write" in names
        assert "purchase.queue" in names
        assert "gmail_configure_oauth_client" not in names
        assert "gmail_oauth_login" not in names

        memory_read = next(t for t in tools if t.name == "memory.read")
        assert memory_read.inputSchema.get("properties", {}).get("key") is not None
        assert memory_read.outputSchema is not None
        assert memory_read.outputSchema.get("type") == "object"
        assert memory_read.annotations is not None
        assert memory_read.annotations.readOnlyHint is True
        assert memory_read.meta is not None
        assert memory_read.meta.get("io.capabledeputy/capability_kind") == "READ_FS"

        purchase = next(t for t in tools if t.name == "purchase.queue")
        assert purchase.annotations is not None
        assert purchase.annotations.destructiveHint is True

        await client.call("shutdown")


async def test_dispatch_tool_allow_returns_output(paths: dict[str, Path]) -> None:
    daemon, app = await _build_daemon(paths)
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        result = await dispatch_tool(
            client,
            s.id,
            "memory.write",
            {"key": "k", "value": "v"},
        )
        assert result.isError is False
        assert len(result.content) == 1
        text = _text(result)
        assert "ok" in text.lower()
        assert result.structuredContent is not None
        assert result.structuredContent.get("ok") is True

        await client.call("shutdown")


async def test_dispatch_tool_deny_returns_policy_message(paths: dict[str, Path]) -> None:
    daemon, app = await _build_daemon(paths)
    s = await app.graph.new()

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        result = await dispatch_tool(
            client,
            s.id,
            "memory.read",
            {"key": "k"},
        )
        assert result.isError is True
        text = _text(result)
        assert "policy denied" in text.lower()
        assert "decision=deny" in text.lower()
        assert result.meta is not None
        assert result.meta.get("io.capabledeputy/decision") == "deny"
        assert "io.capabledeputy/approval_id" in result.meta

        await client.call("shutdown")


async def test_dispatch_tool_includes_labels_added_in_response(paths: dict[str, Path]) -> None:
    daemon, app = await _build_daemon(paths)
    health_tag = CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")
    app.memory.write(
        "labs",
        "x",
        LabelState(a=frozenset({health_tag})),
    )
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        result = await dispatch_tool(
            client,
            s.id,
            "memory.read",
            {"key": "labs"},
        )
        text = _text(result)
        assert "session labels expanded" in text
        assert "confidential.health" in text
        assert result.meta is not None
        assert "confidential.health" in result.meta.get(
            "io.capabledeputy/labels_added",
            [],
        )

        await client.call("shutdown")


async def test_full_mcp_scenario_blocks_egress_after_health_read(
    paths: dict[str, Path],
) -> None:
    """Simulates the canonical scenario as if Claude Code were driving
    via MCP. First call: read health data (allowed, propagates label).
    Second call: try to purchase (denied by health-meets-egress)."""
    daemon, app = await _build_daemon(paths)
    health_tag = CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")
    app.memory.write(
        "rx",
        "lisinopril 10mg",
        LabelState(a=frozenset({health_tag})),
    )
    s = await app.graph.new(intent="claude-code mcp test")
    read_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    purchase_cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=1000,
    )
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({read_cap, purchase_cap}),
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])

        read_result = await dispatch_tool(client, s.id, "memory.read", {"key": "rx"})
        assert read_result.isError is False
        assert "confidential.health" in _text(read_result)

        purchase_result = await dispatch_tool(
            client,
            s.id,
            "purchase.queue",
            {"vendor": "pharmacy", "item": "rx", "amount": 50},
        )
        assert purchase_result.isError is True
        assert "health-meets-egress" in _text(purchase_result)

        await client.call("shutdown")


async def test_build_server_constructs_a_server(paths: dict[str, Path]) -> None:
    from uuid import uuid4

    from capabledeputy.mcp_server.server import build_server

    daemon, _app = await _build_daemon(paths)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        server = await build_server(client, uuid4())
        assert server.name == "capdep"

        await client.call("shutdown")


# --- regression: elicitation call contract (pyright caught a real bug) ---
#
# `_try_elicit_and_approve` previously called session.elicit with
# `requested_schema=ElicitRequestedSchema(...)` — wrong kwarg name AND
# wrong type vs the mcp signature `elicit(message, requestedSchema:
# dict, ...)`. That path would raise at runtime when the email-approval
# elicitation fired; it was invisible because untested. The fake
# session below uses the STRICT real signature, so a regression to the
# old kwarg makes elicit() raise → the helper returns None → this fails.

from types import SimpleNamespace  # noqa: E402

from capabledeputy.mcp_server.server import (  # noqa: E402
    _try_elicit_and_approve,
)


class _StrictSession:
    def __init__(self) -> None:
        self.elicit_kwargs: dict[str, object] | None = None

    async def elicit(
        self,
        message: str,
        requestedSchema: dict,  # noqa: N803
        related_request_id=None,
    ):
        self.elicit_kwargs = {
            "message": message,
            "requestedSchema": requestedSchema,
        }
        return SimpleNamespace(action="accept", content={"approve": True})


async def test_elicit_call_uses_requested_schema_dict(fake_daemon) -> None:
    sess = _StrictSession()
    server = SimpleNamespace(
        request_context=SimpleNamespace(session=sess),
    )
    client = fake_daemon(
        {
            "approval.approve": {
                "approval": {"id": 1},
                "executed_in_session": "ffff0000-0000-0000-0000-000000000000",
                "dispatch": {"decision": "allow", "output": {"ok": True}},
            },
        },
    )
    result = await _try_elicit_and_approve(
        client,  # type: ignore[arg-type]
        server,  # type: ignore[arg-type]
        "email.send",
        {"to": "a@b.com", "subject": "s", "body": "hi"},
        {"rule": "financial-meets-email", "effective_labels": [], "approval_id": 1},
    )
    # The strict-signature fake would have raised on the old kwarg →
    # helper returns None. Reaching a CallToolResult proves the
    # requestedSchema dict contract holds.
    assert result is not None
    assert sess.elicit_kwargs is not None
    rs = sess.elicit_kwargs["requestedSchema"]
    assert isinstance(rs, dict)
    assert rs["type"] == "object" and "properties" in rs and "required" in rs
