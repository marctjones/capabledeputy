from __future__ import annotations

import json
import re
from pathlib import Path

from capabledeputy.mcp_server.control import discover_control_tools

ROOT = Path(__file__).resolve().parents[1]
PARITY_PATH = ROOT / "docs" / "client-parity.json"


def _parity() -> dict:
    return json.loads(PARITY_PATH.read_text(encoding="utf-8"))


def _daemon_methods_from_source() -> set[str]:
    methods: set[str] = set()
    for path in sorted((ROOT / "src" / "capabledeputy" / "daemon").glob("*_handlers.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"[\"']([a-zA-Z0-9_.]+)[\"']\s*:", text):
            key = match.group(1)
            if "." in key or key in {"ping", "version"}:
                methods.add(key)
    handlers = (ROOT / "src" / "capabledeputy" / "daemon" / "handlers.py").read_text(
        encoding="utf-8",
    )
    for match in re.finditer(r"[\"']([a-zA-Z0-9_.]+)[\"']\s*:", handlers):
        key = match.group(1)
        if "." in key or key in {"ping", "version"}:
            methods.add(key)
    lifecycle = (ROOT / "src" / "capabledeputy" / "daemon" / "lifecycle.py").read_text(
        encoding="utf-8",
    )
    for match in re.finditer(r"handlers\[[\"']([a-zA-Z0-9_.]+)[\"']\]", lifecycle):
        methods.add(match.group(1))
    # tool.call is conditional on a tool client, but it is part of the daemon contract.
    methods.add("tool.call")
    # These are audit event names used inside relationship aggregation, not RPC handlers.
    methods.discard("approval.approved")
    methods.discard("approval.denied")
    return methods


def test_client_parity_manifest_covers_daemon_methods() -> None:
    manifest = _parity()["rpc_methods"]
    daemon_methods = _daemon_methods_from_source()

    assert daemon_methods - set(manifest) == set()


def test_client_parity_manifest_has_valid_client_statuses() -> None:
    parity = _parity()
    valid = set(parity["status_values"])
    clients = parity["clients"]
    for method, row in parity["rpc_methods"].items():
        assert row["tier"]
        for client in clients:
            assert row[client] in valid, f"{method} has invalid {client} status"


def test_mcp_control_implements_manifested_methods() -> None:
    manifest = _parity()["rpc_methods"]
    implemented = {
        row["mcp_control"] for row in manifest.values() if row["mcp_control"] == "implemented"
    }
    assert implemented == {"implemented"}

    tools_by_rpc = {tool.name: tool.name for tool in discover_control_tools()}
    # The dispatch table is private, so assert indirectly by names that map to
    # every implemented RPC in the checked-in contract.
    text = (ROOT / "src" / "capabledeputy" / "mcp_server" / "control.py").read_text(
        encoding="utf-8",
    )
    for method, row in manifest.items():
        if row["mcp_control"] == "implemented":
            assert f'"{method}"' in text, f"MCP-control missing {method}"
    assert "tool_call" in tools_by_rpc


def test_swift_gui_implements_manifested_methods() -> None:
    manifest = _parity()["rpc_methods"]
    swift_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "apps" / "macos" / "CapDep" / "Sources").glob("*.swift")
    )
    for method, row in manifest.items():
        if row["swift_gui"] == "implemented":
            assert f'"{method}"' in swift_text, f"Swift GUI missing {method}"
