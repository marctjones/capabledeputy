"""Opt-in real upstream MCP smoke matrix.

This is intentionally skipped by default. Set CAPDEP_REAL_MCP_SMOKE_CONFIG to a
daemon-style YAML file with `upstream_servers` entries to exercise whatever real
servers the operator has installed and authenticated locally.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from capabledeputy.tools.registry import ToolRegistry
from capabledeputy.upstream.config import parse_config
from capabledeputy.upstream.manager import UpstreamManager

pytestmark = pytest.mark.external_mcp


def _smoke_config_path() -> Path | None:
    value = os.environ.get("CAPDEP_REAL_MCP_SMOKE_CONFIG")
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.exists():
        return None
    return path


@pytest.mark.skipif(
    _smoke_config_path() is None,
    reason="set CAPDEP_REAL_MCP_SMOKE_CONFIG to a daemon upstream_servers YAML file",
)
async def test_real_external_mcp_server_matrix_registers_or_rejects_fail_closed() -> None:
    path = _smoke_config_path()
    assert path is not None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    configs = parse_config(raw)
    assert configs, "smoke config must define at least one upstream server"

    registry = ToolRegistry()
    async with UpstreamManager(configs, registry) as manager:
        statuses = manager.server_status

    assert set(statuses) == {cfg.name for cfg in configs}
    for name, status in statuses.items():
        assert status.state in {"registered", "failed"}
        assert status.transport in {"stdio", "streamable_http"}
        if status.state == "registered":
            assert status.registered_tool_count + status.rejected_tool_count > 0, name
        else:
            assert status.error, name

    for tool in registry.list():
        assert tool.name.count(".") >= 1
        assert tool.capability_kind is not None
        assert tool.inherent_tags is not None
