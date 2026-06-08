"""Tests for the MCP resources and prompts surfaces (v0.2 work).

Drives the daemon end-to-end through the existing JSON-RPC client.
Resources reads dispatch through the same LabeledToolClient as the
memory.read tool, so tests verify policy gating + label propagation
on the resources path matches the tool path.
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
from capabledeputy.daemon.memory_handlers import make_memory_handlers
from capabledeputy.daemon.server import Daemon
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.daemon.tool_handlers import make_tool_handlers
from capabledeputy.ipc.client import DaemonClient
from capabledeputy.mcp_server.prompts import get_prompt, list_prompts
from capabledeputy.mcp_server.resources import (
    MEMORY_URI_PREFIX,
    ResourceAccessError,
    list_resources,
    read_resource,
)
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier


def _text_of(content: object) -> str:
    assert isinstance(content, mcp_types.TextContent)
    return content.text


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "socket": tmp_path / "test.sock",
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
    handlers.update(make_tool_handlers(app.registry, app.graph, app.tool_client))
    handlers.update(make_memory_handlers(app))
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


async def test_list_resources_exposes_memory_entries_with_labels(
    paths: dict[str, Path],
) -> None:
    daemon, app = await _build_daemon(paths)
    app.memory.write(
        "rx",
        "lisinopril 10mg",
        LabelState(
            a=frozenset(
                {
                    CategoryTag(
                        "health",
                        Tier.REGULATED,
                        assignment_provenance="source-declared",
                    )
                }
            )
        ),
    )
    app.memory.write(
        "notes",
        "groceries",
        LabelState(
            a=frozenset(
                {
                    CategoryTag(
                        "personal",
                        Tier.REGULATED,
                        assignment_provenance="source-declared",
                    )
                }
            )
        ),
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        resources = await list_resources(client)
        assert len(resources) == 2

        rx = next(r for r in resources if r.uri.path == "/rx" or "rx" in str(r.uri))
        assert str(rx.uri).startswith(MEMORY_URI_PREFIX)
        assert rx.mimeType == "application/json"
        assert rx.meta is not None
        assert "confidential.health" in rx.meta.get("io.capabledeputy/labels", [])

        await client.call("shutdown")


async def test_read_resource_dispatches_through_policy(
    paths: dict[str, Path],
) -> None:
    """A session with a READ_FS capability can read the resource;
    the read goes through LabeledToolClient.call_tool with memory.read,
    so policy gating and label propagation happen on the resources
    path identically to the tools path."""
    daemon, app = await _build_daemon(paths)
    app.memory.write(
        "k",
        "value",
        LabelState(
            a=frozenset(
                {
                    CategoryTag(
                        "health",
                        Tier.REGULATED,
                        assignment_provenance="source-declared",
                    )
                }
            )
        ),
    )
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        text = await read_resource(client, s.id, f"{MEMORY_URI_PREFIX}k")
        assert "value" in text

        after = app.graph.get(s.id)
        assert any(c.category == "health" for c in after.label_state.a)

        await client.call("shutdown")


async def test_read_resource_denied_when_no_capability(
    paths: dict[str, Path],
) -> None:
    daemon, app = await _build_daemon(paths)
    app.memory.write("k", "v", LabelState())
    s = await app.graph.new()

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        with pytest.raises(ResourceAccessError, match="policy denied"):
            await read_resource(client, s.id, f"{MEMORY_URI_PREFIX}k")

        await client.call("shutdown")


async def test_read_resource_unsupported_uri_rejected(paths: dict[str, Path]) -> None:
    daemon, _app = await _build_daemon(paths)
    from uuid import uuid4

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        with pytest.raises(ResourceAccessError, match="unsupported URI"):
            await read_resource(client, uuid4(), "https://example.com/")

        await client.call("shutdown")


async def test_list_prompts_returns_starter_set() -> None:
    prompts = list_prompts()
    names = {p.name for p in prompts}
    assert "prescription-review" in names
    assert "daily-briefing" in names
    assert "safe-share" in names
    assert "untrusted-research" in names

    rx = next(p for p in prompts if p.name == "prescription-review")
    assert rx.title is not None
    args = rx.arguments
    assert args is not None
    arg_names = {a.name for a in args}
    assert "memory_key" in arg_names


async def test_get_prompt_renders_arguments() -> None:
    result = get_prompt(
        "prescription-review",
        {"memory_key": "rx", "recipient": "wife@example.com"},
    )
    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg.role == "user"
    text = _text_of(msg.content)
    assert "'rx'" in text
    assert "wife@example.com" in text


async def test_get_prompt_missing_argument_renders_empty() -> None:
    result = get_prompt("prescription-review", {"memory_key": "rx"})
    text = _text_of(result.messages[0].content)
    assert "'rx'" in text


async def test_get_prompt_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_prompt("does-not-exist", {})


async def test_safe_share_prompt_includes_all_arguments() -> None:
    result = get_prompt(
        "safe-share",
        {
            "memory_key": "labs",
            "recipient": "wife@example.com",
            "justification": "doctor said to share",
        },
    )
    text = _text_of(result.messages[0].content)
    assert "'labs'" in text
    assert "wife@example.com" in text
    assert "doctor said to share" in text


async def test_untrusted_research_prompt_describes_label_constraint() -> None:
    result = get_prompt("untrusted-research", {"query": "best umbrella stroller"})
    text = _text_of(result.messages[0].content)
    assert "untrusted.external" in text
    assert "umbrella" in text
