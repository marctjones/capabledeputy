"""Tests for the per-server yaml loader (Issue #35)."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.upstream.server_yaml import (
    CustomKindDecl,
    CustomKindRegistry,
    InvalidKindNameError,
    KindCollisionError,
    OverrideFile,
    ServerYamlConfig,
    ServerYamlError,
    UnknownOverrideTargetError,
    apply_overrides,
    load_servers_d,
)


def _write_yaml(directory: Path, filename: str, content: str) -> Path:
    p = directory / filename
    p.write_text(content)
    return p


def test_custom_kind_name_must_be_namespaced() -> None:
    """Flat kind names (READ_FS-style) are reserved for capdep core
    built-ins. Custom kinds in config must use namespace:path."""
    with pytest.raises(InvalidKindNameError, match="namespace"):
        CustomKindDecl.from_dict({"name": "DM_SEND"}, filename="bad.yaml")


def test_custom_kind_accepts_namespaced_name() -> None:
    kind = CustomKindDecl.from_dict({"name": "slack:dm.send"}, filename="slack.yaml")
    assert kind.name == "slack:dm.send"


def test_load_single_server_yaml(tmp_path: Path) -> None:
    """Single yaml in servers.d/ loads cleanly."""
    d = tmp_path / "servers.d"
    d.mkdir()
    _write_yaml(d, "slack.yaml", """
schema_version: 1
name: slack
command: ["npx", "slack-mcp-server"]
kinds:
  - name: slack:dm.send
    description: "Send a DM"
    destructive: true
    add_labels: [egress.email]
  - name: slack:read
    destructive: false
tool_mappings:
  send_dm: slack:dm.send
  search_messages: slack:read
""")
    configs, overrides, registry = load_servers_d(d)
    assert len(configs) == 1
    assert configs[0].name == "slack"
    assert len(configs[0].custom_kinds) == 2
    assert "slack:dm.send" in registry.names()
    assert "slack:read" in registry.names()
    assert registry.is_destructive("slack:dm.send") is True
    assert registry.is_destructive("slack:read") is False
    assert len(overrides) == 0


def test_two_files_declaring_same_kind_refused(tmp_path: Path) -> None:
    """Collision detection: two server yamls declaring the same
    custom-kind name raise KindCollisionError."""
    d = tmp_path / "servers.d"
    d.mkdir()
    _write_yaml(d, "a.yaml", """
schema_version: 1
name: a
command: ["x"]
kinds:
  - name: shared:thing
""")
    _write_yaml(d, "b.yaml", """
schema_version: 1
name: b
command: ["y"]
kinds:
  - name: shared:thing
""")
    with pytest.raises(KindCollisionError, match="shared:thing"):
        load_servers_d(d)


def test_unknown_override_target_refused(tmp_path: Path) -> None:
    """An override file referencing a non-existent server is refused."""
    d = tmp_path / "servers.d"
    d.mkdir()
    _write_yaml(d, "slack.yaml", """
schema_version: 1
name: slack
command: ["x"]
""")
    _write_yaml(d, "99-notion-overrides.yaml", """
schema_version: 1
overrides_server: notion
""")
    with pytest.raises(UnknownOverrideTargetError, match="notion"):
        load_servers_d(d)


def test_override_file_patches_kinds(tmp_path: Path) -> None:
    """An override file can patch a kind's labels/destructive without
    modifying the vendor file."""
    d = tmp_path / "servers.d"
    d.mkdir()
    _write_yaml(d, "slack.yaml", """
schema_version: 1
name: slack
command: ["x"]
kinds:
  - name: slack:read
    destructive: false
    add_labels: [untrusted.external]
""")
    _write_yaml(d, "99-slack-overrides.yaml", """
schema_version: 1
overrides_server: slack
kinds:
  - name: slack:read
    destructive: false
    add_labels: [untrusted.external, confidential.financial]
""")
    configs, overrides, registry = load_servers_d(d)
    merged = apply_overrides(configs, overrides)
    assert len(merged) == 1
    # The override's version of slack:read wins
    slack_read = next(k for k in merged[0].custom_kinds if k.name == "slack:read")
    label_strs = {l.value for l in slack_read.add_labels}
    assert "confidential.financial" in label_strs


def test_override_in_non_99_file_refused(tmp_path: Path) -> None:
    """A file with `overrides_server` but a non-99- filename is
    refused — the convention is the load-order signal."""
    d = tmp_path / "servers.d"
    d.mkdir()
    _write_yaml(d, "my-override.yaml", """
schema_version: 1
overrides_server: slack
""")
    with pytest.raises(ServerYamlError, match=r"99-"):
        load_servers_d(d)


def test_missing_servers_d_dir_returns_empty() -> None:
    """No servers.d/ directory means no servers loaded (not an error)."""
    configs, overrides, registry = load_servers_d(Path("/nonexistent"))
    assert configs == []
    assert overrides == []
    assert registry.names() == frozenset()


def test_legacy_tool_overrides_syntax_still_works(tmp_path: Path) -> None:
    """The long-form `tool_overrides:` syntax (used by today's
    daemon.yaml) is still accepted alongside the new short-form
    `tool_mappings:`."""
    d = tmp_path / "servers.d"
    d.mkdir()
    _write_yaml(d, "gws.yaml", """
schema_version: 1
name: gws
command: ["x"]
tool_overrides:
  gmail.list:
    capability_kind: GMAIL_READ
    additional_labels: [untrusted.external]
""")
    configs, _, _ = load_servers_d(d)
    assert "gmail.list" in configs[0].server_config.tool_overrides
    ov = configs[0].server_config.tool_overrides["gmail.list"]
    assert ov.capability_kind is not None
    assert ov.capability_kind.value == "GMAIL_READ"


def test_unsupported_schema_version_refused(tmp_path: Path) -> None:
    """Old/future schema versions raise — operator gets a clear error."""
    d = tmp_path / "servers.d"
    d.mkdir()
    _write_yaml(d, "future.yaml", """
schema_version: 99
name: future
command: ["x"]
""")
    with pytest.raises(ServerYamlError, match="schema_version 99"):
        load_servers_d(d)


def test_registry_distinguishes_destructive_kinds() -> None:
    registry = CustomKindRegistry()
    registry.register(CustomKindDecl(name="x:write", destructive=True, declared_by_file="a"))
    registry.register(CustomKindDecl(name="x:read", destructive=False, declared_by_file="b"))
    assert registry.is_destructive("x:write") is True
    assert registry.is_destructive("x:read") is False
    assert registry.is_destructive("x:unknown") is False


def test_namespace_prefix_isolation() -> None:
    """Two plugins using their own namespace shouldn't collide even
    if they pick the same path component."""
    registry = CustomKindRegistry()
    registry.register(CustomKindDecl(name="slack:dm.send", declared_by_file="slack.yaml"))
    registry.register(CustomKindDecl(name="discord:dm.send", declared_by_file="discord.yaml"))
    # Both registered cleanly
    assert "slack:dm.send" in registry.names()
    assert "discord:dm.send" in registry.names()
