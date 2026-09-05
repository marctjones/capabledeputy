from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.audit.events import EventType
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import LabelState
from capabledeputy.policy.rules import Decision


def test_native_tool_registry_entries_carry_policy_metadata(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )

    tools = app.registry.list()

    assert tools
    for tool in tools:
        assert tool.operations, f"{tool.name} must declare effect operations"
        assert tool.risk_ids, f"{tool.name} must cite at least one risk id"
        assert tool.capability_kind, f"{tool.name} must declare capability kind"


def test_every_outbound_native_tool_declares_egress_effect(tmp_path: Path) -> None:
    """#294 / #298 — audit: every registered outbound-capable tool must declare
    an egress-capable operation, so egress-ness is carried by the tool's own
    declaration rather than a hand-maintained kind list at the chokepoint. This
    makes the Rule-8 registration guarantee explicit at the app level and guards
    against a future tool (native, MCP-adapted, or skill) re-introducing the
    web.fetch drift (#293). App.startup() would already refuse to register a
    violator; this asserts the invariant holds for the full shipped surface."""
    from capabledeputy.policy.effect_class import effect_class_is_egress_capable
    from capabledeputy.tools.registry import _KIND_TO_EFFECT

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    for tool in app.registry.list():
        kind_effect = _KIND_TO_EFFECT.get(str(tool.capability_kind))
        if kind_effect is None or not effect_class_is_egress_capable(kind_effect):
            continue  # not an outbound-capable kind
        declared = {op.effect_class for op in tool.operations}
        assert any(effect_class_is_egress_capable(e) for e in declared), (
            f"{tool.name}: outbound capability {tool.capability_kind} must declare "
            f"an egress-capable operation (declared: {sorted(str(e) for e in declared)})"
        )


@pytest.mark.asyncio
async def test_policy_decided_is_emitted_before_every_dispatched_tool(
    tmp_path: Path,
) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    session = await app.graph.new()
    await app.graph.grant_capability(
        session.id,
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    )
    app.memory.write("note", "hello", LabelState())

    outcome = await app.tool_client.call_tool(session.id, "memory.read", {"key": "note"})

    assert outcome.decision == Decision.ALLOW
    events = [
        event.event_type
        for event in await app.audit.read_all()
        if event.event_type in {EventType.POLICY_DECIDED, EventType.TOOL_DISPATCHED}
    ]
    assert events[:2] == [EventType.POLICY_DECIDED, EventType.TOOL_DISPATCHED]


@pytest.mark.asyncio
async def test_every_registered_tool_denies_without_authority(tmp_path: Path, monkeypatch) -> None:
    """Exercise every shipped registration without invoking any real actuator."""
    from dataclasses import replace
    from unittest.mock import AsyncMock

    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    await app.startup()
    session = await app.graph.new()
    called = AsyncMock(side_effect=AssertionError("unauthorized actuator reached"))
    tools = app.registry.list()
    assert tools
    for tool in tools:
        monkeypatch.setitem(app.registry._tools, tool.name, replace(tool, handler=called))
        outcome = await app.tool_client.call_tool(session.id, tool.name, {})
        assert outcome.decision == Decision.DENY, tool.name
    called.assert_not_awaited()


def test_tool_handler_invocation_stays_at_dispatch_boundary() -> None:
    """Tripwire for direct handler calls; bundled MCP adapters are substrate."""
    import ast

    root = Path("src/capabledeputy")
    allowed = {"tools/client.py", "mcp_servers/_common.py"}
    violations = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "handler"
                and path.relative_to(root).as_posix() not in allowed
            ):
                violations.append(f"{path}:{node.lineno}")
    assert not violations, "Direct tool handler invocation: " + ", ".join(violations)


def test_interactive_clients_do_not_import_authority_implementations() -> None:
    """Keep stores/actuators out of clients; allow named read-only helpers."""
    import ast

    root = Path("src/capabledeputy")
    forbidden = (
        "capabledeputy.app",
        "capabledeputy.store",
        "capabledeputy.session.graph",
        "capabledeputy.tools.client",
        "capabledeputy.daemon",
        "sqlite3",
    )
    allowed = {
        ("cli/main.py", "capabledeputy.daemon.lifecycle"),  # host-owned daemon lifecycle
        ("cli/chat.py", "capabledeputy.daemon.handlers"),  # code revision metadata
        ("tui/app.py", "capabledeputy.daemon.bundle_handlers"),  # pure impact decoding
    }
    violations = []
    for directory in (root / "cli", root / "tui"):
        for path in directory.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                modules = []
                if isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                for module in modules:
                    if (
                        any(
                            module == prefix or module.startswith(prefix + ".")
                            for prefix in forbidden
                        )
                        and (path.relative_to(root).as_posix(), module) not in allowed
                    ):
                        violations.append(f"{path}:{getattr(node, 'lineno', 0)}: {module}")
    assert not violations, "Client authority imports: " + ", ".join(violations)
