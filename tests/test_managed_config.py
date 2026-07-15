"""Tests for the user-local daemon config helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capabledeputy.cli._managed_config import (
    IMAP_BLOCK_BODY,
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
    new_body = '  - name: mail2\n    command: ["echo", "hi"]\n    strict: true\n'
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
        "  - name: gws\n"
        '    command: ["npx", "gws-mcp-server", "--services", "gmail"]\n'
        "    strict: false\n"
    )
    write_managed_block(path, "gworkspace", gworkspace_body)
    text = path.read_text(encoding="utf-8")
    assert "# BEGIN capdep-managed: imap" in text
    assert "# BEGIN capdep-managed: gworkspace" in text
    parsed = yaml.safe_load(text)
    names = {s["name"] for s in parsed["upstream_servers"]}
    assert {"mail", "gws"}.issubset(names)


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


# --- Default-assistant surface + top-level sandbox block --------------------


def test_register_default_assistant_surface_writes_all_blocks(xdg_tmp: Path) -> None:
    from capabledeputy.cli._managed_config import (
        BUNDLED_FETCH_BLOCK_ID,
        BUNDLED_FS_BLOCK_ID,
        BUNDLED_GIT_BLOCK_ID,
        BUNDLED_IMAGE_FETCH_BLOCK_ID,
        BUNDLED_IMAGE_GENERATE_BLOCK_ID,
        BUNDLED_MEMORY_BLOCK_ID,
        BUNDLED_SEARCH_BLOCK_ID,
        register_default_assistant_surface,
    )

    path = user_default_daemon_config_path()
    # include_sandbox=False to keep the test podman-binary-independent
    msgs = register_default_assistant_surface(path, include_sandbox=False)
    text = path.read_text(encoding="utf-8")
    for block_id in (
        BUNDLED_FS_BLOCK_ID,
        BUNDLED_MEMORY_BLOCK_ID,
        BUNDLED_GIT_BLOCK_ID,
        BUNDLED_FETCH_BLOCK_ID,
        BUNDLED_SEARCH_BLOCK_ID,
        BUNDLED_IMAGE_FETCH_BLOCK_ID,
        BUNDLED_IMAGE_GENERATE_BLOCK_ID,
    ):
        assert f"# BEGIN capdep-managed: {block_id}" in text
        assert f"# END capdep-managed: {block_id}" in text
    # The file parses as YAML and lists all five upstream servers
    parsed = yaml.safe_load(text)
    names = {s["name"] for s in parsed["upstream_servers"]}
    assert {
        "bundled-fs",
        "bundled-memory",
        "bundled-git",
        "bundled-fetch",
        "bundled-search",
        "bundled-image-fetch",
        "bundled-image-generate",
    }.issubset(names)
    # status messages report one line per block + a sandbox-skipped message
    assert any("bundled-fs" in m for m in msgs)
    assert any("sandbox skipped" in m for m in msgs)


def test_register_default_assistant_surface_idempotent(xdg_tmp: Path) -> None:
    from capabledeputy.cli._managed_config import register_default_assistant_surface

    path = user_default_daemon_config_path()
    register_default_assistant_surface(path, include_sandbox=False)
    first = path.read_text(encoding="utf-8")
    msgs = register_default_assistant_surface(path, include_sandbox=False)
    assert path.read_text(encoding="utf-8") == first
    # Every entry says "already up to date"
    assert all("already up to date" in m or "sandbox skipped" in m for m in msgs)


def test_register_default_coexists_with_imap_block(xdg_tmp: Path) -> None:
    """imap-setup writes the IMAP block; running register_default
    afterwards must not disturb it (and vice versa)."""
    from capabledeputy.cli._managed_config import register_default_assistant_surface

    path = user_default_daemon_config_path()
    write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    register_default_assistant_surface(path, include_sandbox=False)
    text = path.read_text(encoding="utf-8")
    assert "# BEGIN capdep-managed: imap" in text
    assert "# BEGIN capdep-managed: bundled-fs" in text
    parsed = yaml.safe_load(text)
    names = {s["name"] for s in parsed["upstream_servers"]}
    assert "mail" in names  # imap server's name
    assert "bundled-fs" in names


def test_sandbox_top_level_block(xdg_tmp: Path) -> None:
    """The sandbox block lives at the top of the YAML, NOT under
    upstream_servers:. Verify it parses cleanly + sits alongside the
    upstream entries."""
    from capabledeputy.cli._managed_config import (
        SANDBOX_BLOCK_BODY,
        SANDBOX_BLOCK_ID,
        register_default_assistant_surface,
        write_top_level_managed_block,
    )

    path = user_default_daemon_config_path()
    register_default_assistant_surface(path, include_sandbox=False)
    write_top_level_managed_block(path, SANDBOX_BLOCK_ID, SANDBOX_BLOCK_BODY)
    text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    # Both top-level keys present
    assert "upstream_servers" in parsed
    assert "sandbox" in parsed
    assert parsed["sandbox"]["provider"] == "podman"
    assert any(r["id"] == "scratch" for r in parsed["sandbox"]["regions"])


def test_sandbox_top_level_block_replace_in_place(xdg_tmp: Path) -> None:
    from capabledeputy.cli._managed_config import write_top_level_managed_block

    path = user_default_daemon_config_path()
    write_top_level_managed_block(
        path,
        "sandbox",
        "sandbox:\n  provider: podman\n  regions: []\n",
    )
    original = path.read_text(encoding="utf-8")
    replaced, changed = write_top_level_managed_block(
        path,
        "sandbox",
        "sandbox:\n  provider: podman\n  regions: []\n",
    )
    assert replaced is True
    assert changed is False  # idempotent
    assert path.read_text(encoding="utf-8") == original


def test_podman_available_falls_back_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `podman` is not on PATH, podman_available() returns False."""
    import shutil

    from capabledeputy.cli._managed_config import podman_available

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert podman_available() is False


def _podman_runner(version_rc: int, info_rc: int):
    """A command runner that reports the given exit codes for `podman --version`
    vs `podman info`, so readiness can be tested without a real Podman."""
    import subprocess

    def runner(command):
        rc = version_rc if command[-1] == "--version" else info_rc
        out = "podman version 5.0\n" if command[-1] == "--version" else "{}"
        return subprocess.CompletedProcess(list(command), rc, stdout=out, stderr="")

    return runner


def test_podman_ready_requires_info_not_just_version() -> None:
    """#361 — the exact trap the reconciliation fixes: `podman --version` can
    succeed while the machine is DOWN. `podman_ready()` must be False then, so
    the sandbox block is not written into a state where SEALED fails at runtime."""
    from capabledeputy.cli._managed_config import podman_readiness, podman_ready

    assert podman_ready(_podman_runner(0, 0)) is True
    assert podman_ready(_podman_runner(0, 1)) is False  # version ok, machine down
    assert podman_ready(_podman_runner(127, 0)) is False  # not installed

    assert podman_readiness(_podman_runner(0, 1))[0] == "machine_not_running"
    assert podman_readiness(_podman_runner(127, 0))[0] == "not_installed"


def test_assistant_surface_autodetect_uses_deep_readiness(
    xdg_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#361 — `register_default_assistant_surface` auto-detect (include_sandbox
    is None) must consult the deep readiness, not `--version`. Machine down ⇒
    sandbox skipped, even though podman is installed."""
    import capabledeputy.cli._managed_config as mc

    path = xdg_tmp / "daemon.yaml"

    # Machine down: version ok, info fails → skip the block.
    monkeypatch.setattr(mc, "podman_readiness", lambda runner=None: ("machine_not_running", ""))
    msgs = mc.register_default_assistant_surface(path)
    assert any("sandbox skipped" in m for m in msgs)
    assert "sandbox" not in path.read_text(encoding="utf-8")

    # Ready: version + info both ok → write the block.
    path2 = xdg_tmp / "daemon2.yaml"
    monkeypatch.setattr(mc, "podman_readiness", lambda runner=None: ("ready", "podman 5.0"))
    msgs2 = mc.register_default_assistant_surface(path2)
    assert any("sandbox" in m and "skipped" not in m for m in msgs2)
    assert "sandbox" in path2.read_text(encoding="utf-8")


# --- Google Workspace block via official `gws mcp` ----


def test_gworkspace_block_writes_managed_section(xdg_tmp: Path) -> None:
    from capabledeputy.cli._managed_config import (
        GWORKSPACE_BLOCK_BODY,
        GWORKSPACE_BLOCK_ID,
    )

    path = user_default_daemon_config_path()
    replaced, changed = write_managed_block(path, GWORKSPACE_BLOCK_ID, GWORKSPACE_BLOCK_BODY)
    assert replaced is False
    assert changed is True
    text = path.read_text(encoding="utf-8")
    assert "# BEGIN capdep-managed: gworkspace" in text
    assert "# END capdep-managed: gworkspace" in text
    parsed = yaml.safe_load(text)
    # Official Google Workspace is registered as separate remote MCP servers.
    names = [s["name"] for s in parsed["upstream_servers"]]
    assert {
        "google-gmail",
        "google-drive",
        "google-calendar",
        "google-chat",
        "google-people",
    }.issubset(names)
    gmail_entry = next(s for s in parsed["upstream_servers"] if s["name"] == "google-gmail")
    assert gmail_entry["transport"] == "streamable_http"
    assert gmail_entry["url"] == "https://gmailmcp.googleapis.com/mcp/v1"
    assert gmail_entry["auth"]["type"] == "oauth2"
    assert gmail_entry["auth"]["client_id_env"] == "GOOGLE_MCP_CLIENT_ID"
    assert gmail_entry["auth"]["extra_authorize_params"]["access_type"] == "offline"
    assert gmail_entry["tool_overrides"]["create_draft"]["capability_kind"] == "GMAIL_DRAFT"
    assert gmail_entry["tool_overrides"]["create_draft"]["target_arg"] == "to"
    assert gmail_entry["tool_overrides"]["search_threads"]["capability_kind"] == "GMAIL_READ"
    calendar_entry = next(s for s in parsed["upstream_servers"] if s["name"] == "google-calendar")
    assert calendar_entry["tool_overrides"]["create_event"]["target_template"] == (
        "gcal://calendar/{calendar_id}/events/attendees/{attendees}"
    )
    chat_entry = next(s for s in parsed["upstream_servers"] if s["name"] == "google-chat")
    assert chat_entry["tool_overrides"]["send_message"]["capability_kind"] == "SEND_MESSAGE"


def test_gworkspace_community_block_still_available() -> None:
    from capabledeputy.cli._managed_config import GWORKSPACE_COMMUNITY_BLOCK_BODY

    parsed = yaml.safe_load("upstream_servers:\n" + GWORKSPACE_COMMUNITY_BLOCK_BODY)
    [entry] = parsed["upstream_servers"]
    assert entry["name"] == "gws"
    assert entry["command"][0] == "npx"
    assert entry["command"][1] == "gws-mcp-server"
    assert entry["tool_overrides"]["drive_delete_file"]["capability_kind"] == "DELETE_FS"


def test_gworkspace_block_coexists_with_imap_and_bundled(xdg_tmp: Path) -> None:
    """All three setup commands write into the same daemon.yaml and
    their managed blocks must not collide."""
    from capabledeputy.cli._managed_config import (
        GWORKSPACE_BLOCK_BODY,
        GWORKSPACE_BLOCK_ID,
        register_default_assistant_surface,
    )

    path = user_default_daemon_config_path()
    write_managed_block(path, "imap", IMAP_BLOCK_BODY)
    register_default_assistant_surface(path, include_sandbox=False)
    write_managed_block(path, GWORKSPACE_BLOCK_ID, GWORKSPACE_BLOCK_BODY)

    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    names = {s["name"] for s in parsed["upstream_servers"]}
    # Bundled five + imap (named `mail`) + official Google Workspace servers.
    assert {
        "mail",
        "bundled-fs",
        "bundled-memory",
        "bundled-git",
        "bundled-fetch",
        "bundled-search",
        "google-gmail",
        "google-drive",
        "google-calendar",
        "google-chat",
        "google-people",
    }.issubset(names)


def test_gws_cli_available_returns_false_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    from capabledeputy.cli._managed_config import gws_cli_available

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert gws_cli_available() is False


def test_resolve_upstream_spawn_command_splits_image_servers() -> None:
    from capabledeputy.cli._managed_config import (
        _capdep_executable,
        resolve_upstream_spawn_command,
    )

    gen = resolve_upstream_spawn_command(
        ("capdep-image-generate", "mcp-server-image-generate"),
    )
    fetch = resolve_upstream_spawn_command(
        ("capdep-image-fetch", "mcp-server-image-fetch"),
    )
    assert gen[-1] == "capabledeputy.mcp_servers.image_generate"
    if _capdep_executable():
        assert fetch == (_capdep_executable(), "mcp-server-image-fetch")
    else:
        assert fetch[-1] == "capabledeputy.mcp_servers.image_fetch"
