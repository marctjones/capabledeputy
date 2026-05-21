"""Tests for the user-local daemon config helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from capabledeputy.cli._managed_config import (
    IMAP_BLOCK_BODY,
    IMAP_BLOCK_ID,
    has_managed_block,
    imap_credentials_present,
    remove_managed_block,
    resolve_daemon_config_with_source,
    user_default_daemon_config_path,
    write_managed_block,
)


@pytest.fixture
def xdg_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect XDG_CONFIG_HOME so `~/.config/capabledeputy/...`
    resolves under tmp_path for the whole test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("CAPDEP_CONFIG", raising=False)
    return tmp_path / "capabledeputy"


def test_first_write_creates_file_with_header(xdg_tmp: Path) -> None:
    path = user_default_daemon_config_path()
    assert not path.exists()
    replaced, changed = write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    assert replaced is False
    assert changed is True
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "managed by `capdep imap-setup`" in text
    assert "upstream_servers:" in text
    assert "# BEGIN capdep-managed: imap" in text
    assert "# END capdep-managed: imap" in text
    # The whole file must round-trip as YAML so the daemon can load it.
    parsed = yaml.safe_load(text)
    assert parsed is not None
    assert any(s.get("name") == "mail" for s in parsed["upstream_servers"])


def test_rewrite_same_body_is_noop(xdg_tmp: Path) -> None:
    path = user_default_daemon_config_path()
    write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    original = path.read_text(encoding="utf-8")
    replaced, changed = write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    assert replaced is True
    assert changed is False
    assert path.read_text(encoding="utf-8") == original


def test_rewrite_different_body_replaces_in_place(xdg_tmp: Path) -> None:
    path = user_default_daemon_config_path()
    write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    new_body = "  - name: mail2\n    command: [\"echo\", \"hi\"]\n    strict: true\n"
    replaced, changed = write_managed_block(path, "imap", new_body)
    assert replaced is True
    assert changed is True
    text = path.read_text(encoding="utf-8")
    assert "mail2" in text
    assert text.count("# BEGIN capdep-managed: imap") == 1


def test_user_authored_lines_outside_block_are_preserved(xdg_tmp: Path) -> None:
    path = user_default_daemon_config_path()
    write_managed_block(path, "imap", IMAP_BLOCK_BODY)

    # Operator hand-edits the file: adds a comment and a separate
    # upstream entry after the managed block.
    original = path.read_text(encoding="utf-8")
    hand_edited = (
        original
        + "\n  # my own upstream below\n"
        + '  - name: my_local\n    command: ["echo", "hi"]\n    strict: true\n'
    )
    path.write_text(hand_edited, encoding="utf-8")

    # Re-register with the SAME body — must not touch the hand-edit.
    replaced, changed = write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    assert replaced is True
    assert changed is False
    final = path.read_text(encoding="utf-8")
    assert "my own upstream below" in final
    assert "my_local" in final


def test_two_blocks_coexist(xdg_tmp: Path) -> None:
    path = user_default_daemon_config_path()
    write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    gworkspace_body = (
        '  - name: gworkspace\n'
        '    command: ["capdep", "mcp-server-gworkspace"]\n'
        "    strict: true\n"
    )
    write_managed_block(path, "gworkspace", gworkspace_body)
    text = path.read_text(encoding="utf-8")
    assert "# BEGIN capdep-managed: imap" in text
    assert "# BEGIN capdep-managed: gworkspace" in text
    parsed = yaml.safe_load(text)
    names = {s["name"] for s in parsed["upstream_servers"]}
    assert {"mail", "gworkspace"}.issubset(names)


def test_has_managed_block(xdg_tmp: Path) -> None:
    path = user_default_daemon_config_path()
    assert has_managed_block(path, "imap") is False
    write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    assert has_managed_block(path, "imap") is True
    assert has_managed_block(path, "gworkspace") is False


def test_remove_managed_block(xdg_tmp: Path) -> None:
    path = user_default_daemon_config_path()
    write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    assert remove_managed_block(path, "imap") is True
    assert has_managed_block(path, "imap") is False
    # Idempotent: removing again is a no-op
    assert remove_managed_block(path, "imap") is False


def test_resolve_explicit_wins(xdg_tmp: Path, tmp_path: Path) -> None:
    explicit = tmp_path / "alt.yaml"
    explicit.write_text("upstream_servers: []\n", encoding="utf-8")
    # Even if the user default exists, explicit takes precedence.
    write_managed_block(user_default_daemon_config_path(), "imap", IMAP_BLOCK_BODY)
    path, source = resolve_daemon_config_with_source(str(explicit))
    assert path == explicit
    assert source == "explicit"


def test_resolve_env_var(xdg_tmp: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / "env.yaml"
    env_path.write_text("upstream_servers: []\n", encoding="utf-8")
    monkeypatch.setenv("CAPDEP_CONFIG", str(env_path))
    path, source = resolve_daemon_config_with_source(None)
    assert path == env_path
    assert source == "env"


def test_resolve_falls_through_to_user_default(xdg_tmp: Path) -> None:
    path = user_default_daemon_config_path()
    assert not path.exists()
    # No user-default file exists yet.
    found, source = resolve_daemon_config_with_source(None)
    assert found is None
    assert source == "none"

    # After registration, it's discovered.
    write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    found, source = resolve_daemon_config_with_source(None)
    assert found == path
    assert source == "user-default"


def test_imap_credentials_present(xdg_tmp: Path) -> None:
    assert imap_credentials_present() is False
    secrets_dir = xdg_tmp / "secrets"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "imap-config.yaml").write_text("imap: {}\n", encoding="utf-8")
    assert imap_credentials_present() is True
