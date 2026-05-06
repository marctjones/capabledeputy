from pathlib import Path

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.daemon.tool_handlers import make_tool_handlers
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.native.memory import LabeledMemoryStore, make_memory_tools
from capabledeputy.tools.native.purchase import PurchaseQueue, make_purchase_tools
from capabledeputy.tools.registry import ToolRegistry


@pytest.fixture
def writer(tmp_path: Path) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


def _registry_with_natives() -> ToolRegistry:
    registry = ToolRegistry()
    for t in make_memory_tools(LabeledMemoryStore()):
        registry.register(t)
    for t in make_purchase_tools(PurchaseQueue()):
        registry.register(t)
    return registry


async def test_tool_list_returns_native_tools(writer: AuditWriter) -> None:
    registry = _registry_with_natives()
    graph = SessionGraph(audit=writer)
    handlers = make_tool_handlers(registry, graph)

    result = await handlers["tool.list"]({})
    names = {t["name"] for t in result["tools"]}
    assert "memory.read" in names
    assert "memory.write" in names
    assert "purchase.queue" in names


async def test_tool_show_returns_metadata(writer: AuditWriter) -> None:
    registry = _registry_with_natives()
    graph = SessionGraph(audit=writer)
    handlers = make_tool_handlers(registry, graph)

    result = await handlers["tool.show"]({"name": "purchase.queue"})
    assert result["name"] == "purchase.queue"
    assert result["capability_kind"] == CapabilityKind.QUEUE_PURCHASE.value
    assert result["amount_arg"] == "amount"
    assert result["target_arg"] == "vendor"


async def test_tool_test_simulates_decision(writer: AuditWriter) -> None:
    registry = _registry_with_natives()
    graph = SessionGraph(audit=writer)
    handlers = make_tool_handlers(registry, graph)

    s = await graph.new()
    result = await handlers["tool.test"](
        {
            "tool": "memory.read",
            "session_id": str(s.id),
            "args": {"key": "anything"},
        },
    )
    assert result["decision"] == "deny"
    assert "no matching capability" in result["reason"]
    assert result["tool"]["name"] == "memory.read"


async def test_tool_test_decision_allow(writer: AuditWriter) -> None:
    from dataclasses import replace

    from capabledeputy.policy.capabilities import Capability

    registry = _registry_with_natives()
    graph = SessionGraph(audit=writer)
    handlers = make_tool_handlers(registry, graph)

    s = await graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s_with_cap = replace(s, capability_set=frozenset({cap}))
    graph._sessions[s.id] = s_with_cap

    result = await handlers["tool.test"](
        {
            "tool": "memory.read",
            "session_id": str(s.id),
            "args": {"key": "k"},
        },
    )
    assert result["decision"] == "allow"


async def test_tool_call_dispatches_when_allowed(writer: AuditWriter) -> None:
    from dataclasses import replace

    from capabledeputy.policy.capabilities import Capability
    from capabledeputy.tools.client import LabeledToolClient

    registry = _registry_with_natives()
    graph = SessionGraph(audit=writer)
    client = LabeledToolClient(registry, graph, writer)
    handlers = make_tool_handlers(registry, graph, client)

    assert "tool.call" in handlers

    s = await graph.new()
    cap_w = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    cap_r = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    graph._sessions[s.id] = replace(s, capability_set=frozenset({cap_w, cap_r}))

    write_result = await handlers["tool.call"](
        {
            "session_id": str(s.id),
            "tool": "memory.write",
            "args": {"key": "k", "value": "v"},
        },
    )
    assert write_result["decision"] == "allow"
    assert write_result["output"] == {"ok": True, "key": "k"}

    read_result = await handlers["tool.call"](
        {
            "session_id": str(s.id),
            "tool": "memory.read",
            "args": {"key": "k"},
        },
    )
    assert read_result["decision"] == "allow"
    assert read_result["output"] == {"found": True, "value": "v"}


async def test_tool_call_denied_returns_decision(writer: AuditWriter) -> None:
    from capabledeputy.tools.client import LabeledToolClient

    registry = _registry_with_natives()
    graph = SessionGraph(audit=writer)
    client = LabeledToolClient(registry, graph, writer)
    handlers = make_tool_handlers(registry, graph, client)

    s = await graph.new()
    result = await handlers["tool.call"](
        {
            "session_id": str(s.id),
            "tool": "memory.read",
            "args": {"key": "k"},
        },
    )
    assert result["decision"] == "deny"
    assert "no matching capability" in (result["reason"] or "")


async def test_tool_call_absent_when_no_client_supplied() -> None:
    registry = _registry_with_natives()
    graph = SessionGraph()
    handlers = make_tool_handlers(registry, graph)
    assert "tool.call" not in handlers
