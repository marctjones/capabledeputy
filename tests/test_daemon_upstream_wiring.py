"""WI-4: the daemon loads upstream MCP servers from its config file.

Two layers:
  - deterministic unit tests for the opt-in config-path resolution
    (`_resolve_daemon_config`), and
  - an opt-in integration test that actually spawns the real official
    `mcp-server-fetch` subprocess through `UpstreamManager` and proves
    its tools register into a shared registry behind the policy gate.
    Skipped unless CAPDEP_RUN_NETWORK_TESTS=1 (and uvx present) so CI
    stays deterministic.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from capabledeputy.daemon.lifecycle import _resolve_daemon_config

_RUN_NET = pytest.mark.skipif(
    os.environ.get("CAPDEP_RUN_NETWORK_TESTS") != "1" or shutil.which("uvx") is None,
    reason="set CAPDEP_RUN_NETWORK_TESTS=1 and have uvx on PATH",
)


def test_resolve_explicit_path(tmp_path: Path) -> None:
    cfg = tmp_path / "d.yaml"
    cfg.write_text("upstream_servers: []\n", encoding="utf-8")
    assert _resolve_daemon_config(cfg) == cfg


def test_resolve_missing_path_is_none(tmp_path: Path) -> None:
    assert _resolve_daemon_config(tmp_path / "nope.yaml") is None


def test_resolve_none_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPDEP_CONFIG", raising=False)
    assert _resolve_daemon_config(None) is None


def test_resolve_env_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = tmp_path / "env.yaml"
    cfg.write_text("upstream_servers: []\n", encoding="utf-8")
    monkeypatch.setenv("CAPDEP_CONFIG", str(cfg))
    assert _resolve_daemon_config(None) == cfg
    # Explicit arg wins over env.
    other = tmp_path / "arg.yaml"
    other.write_text("upstream_servers: []\n", encoding="utf-8")
    assert _resolve_daemon_config(other) == other


@_RUN_NET
async def test_real_fetch_server_registers_behind_policy(
    tmp_path: Path,
) -> None:
    """End-to-end: spawn the real official fetch MCP server and confirm
    its tool registers into a shared registry with WEB_FETCH +
    untrusted.external (so the policy engine's egress rules apply)."""
    from capabledeputy.policy.capabilities import CapabilityKind
    from capabledeputy.policy.labels import Label
    from capabledeputy.tools.registry import ToolRegistry
    from capabledeputy.upstream.config import UpstreamServerConfig
    from capabledeputy.upstream.manager import UpstreamManager

    registry = ToolRegistry()
    cfg = UpstreamServerConfig(
        name="fetch",
        command=("uvx", "mcp-server-fetch"),
        inherent_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
        tool_overrides={},
        strict=True,
    )
    async with UpstreamManager([cfg], registry):
        fetch_tools = [t for t in registry.list() if t.name.startswith("fetch.")]
        assert fetch_tools, [t.name for t in registry.list()]
        tool = fetch_tools[0]
        assert tool.capability_kind == CapabilityKind.WEB_FETCH
        assert Label.UNTRUSTED_EXTERNAL in tool.inherent_labels
