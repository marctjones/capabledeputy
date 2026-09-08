"""Guard: the shipped curated MCP catalog stays parseable and locked.

These configs let CapableDeputy drive real upstream MCP servers behind
the policy engine. The catalog's security guarantee rests on two
invariants this test pins:

  - every config parses (no CapabilityKind / Label typo silently
    dropping a tool override), and
  - every server is strict (fail-closed admission).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.upstream.config import load_config_file

_CURATED = Path(__file__).parent.parent / "configs" / "curated"
_FILES = sorted(_CURATED.glob("*.yaml"))


def test_curated_dir_is_present() -> None:
    assert _FILES, f"no curated configs found under {_CURATED}"


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_curated_config_parses_and_is_strict(path: Path) -> None:
    configs = load_config_file(path)
    assert configs, f"{path.name} parsed to zero servers"
    for c in configs:
        assert c.strict is True, f"{path.name}:{c.name} is not strict (fail-open)"
        if c.transport == "stdio":
            assert c.command, f"{path.name}:{c.name} has empty command"
        else:
            assert c.url, f"{path.name}:{c.name} has empty remote url"


def test_microsoft_365_disables_outbound_send() -> None:
    """#329 — M365 must not ship a live outbound-send surface.
    send_mail stays mapped (pinned/known) but SEND_EMAIL is disabled at
    admission."""
    [m365] = load_config_file(_CURATED / "microsoft-365.yaml")
    assert "SEND_EMAIL" in m365.disabled_kinds
    # Still pinned so an unmapped send_* can't slip through unnoticed.
    assert m365.tool_overrides["send_mail"].capability_kind is not None
    assert m365.tool_overrides["send_mail"].capability_kind.value == "SEND_EMAIL"


def test_placeholder_endpoints_are_marked_not_connectable() -> None:
    """#329 — M365 + Notion use example.* placeholder URLs; the header must say
    so honestly rather than implying a connectable integration."""
    for name in ("microsoft-365.yaml", "notion.yaml"):
        text = (_CURATED / name).read_text(encoding="utf-8")
        assert ".example" in text  # still a placeholder
        assert "NOT CONNECTABLE AS SHIPPED" in text  # and honestly labeled


def test_legacy_news_server_removed() -> None:
    assert not (
        Path(__file__).parent.parent / "src" / "capabledeputy" / "mcp_servers" / "news.py"
    ).exists()


def test_legacy_github_config_replaced_by_official_remote() -> None:
    assert not (_CURATED / "multi-credential-github.yaml").exists()

    [config] = load_config_file(_CURATED / "github.yaml")
    assert config.transport == "streamable_http"
    assert config.url == "https://api.githubcopilot.com/mcp/"
    assert config.auth is not None
    assert config.auth.type == "oauth2"
    assert config.auth.client_id_env == "GITHUB_MCP_CLIENT_ID"
    assert (
        config.auth.protected_resource_metadata_url
        == "https://api.githubcopilot.com/.well-known/oauth-protected-resource/mcp"
    )
    assert config.tool_overrides["merge_pull_request"].capability_kind is not None


def test_tier1_curated_mappings_cover_core_providers() -> None:
    assert {
        "github.yaml",
        "microsoft-365.yaml",
        "notion.yaml",
    } <= {path.name for path in _FILES}


@pytest.mark.parametrize(
    ("filename", "server_name", "client_env", "write_tool"),
    [
        (
            "microsoft-365.yaml",
            "microsoft-365",
            "MICROSOFT_MCP_CLIENT_ID",
            "update_event",
        ),
        ("notion.yaml", "notion", "NOTION_MCP_CLIENT_ID", "update_page"),
    ],
)
def test_tier1_mapping_fixtures_are_strict_oauth_configs(
    filename: str,
    server_name: str,
    client_env: str,
    write_tool: str,
) -> None:
    [config] = load_config_file(_CURATED / filename)

    assert config.name == server_name
    assert config.transport == "streamable_http"
    assert config.strict is True
    assert config.auth is not None
    assert config.auth.type == "oauth2"
    assert config.auth.client_id_env == client_env
    assert config.tool_overrides
    assert config.tool_overrides[write_tool].capability_kind is not None


def test_slack_uses_official_remote_mcp() -> None:
    [config] = load_config_file(_CURATED / "slack.yaml")

    assert config.transport == "streamable_http"
    assert config.url == "https://mcp.slack.com/mcp"
    assert config.auth is not None
    assert config.auth.type == "oauth2"
    assert config.auth.client_id_env == "SLACK_MCP_CLIENT_ID"
    assert (
        config.auth.authorization_metadata_url
        == "https://mcp.slack.com/.well-known/oauth-authorization-server"
    )
    assert config.tool_overrides["send_message"].capability_kind is not None


def test_kagi_uses_official_package_and_only_web_fetch_tools() -> None:
    [config] = load_config_file(_CURATED / "kagi.yaml")

    assert config.command == ("uvx", "kagimcp")
    assert sorted(config.tool_overrides) == ["kagi_extract", "kagi_search_fetch"]
    assert {
        override.capability_kind.value
        for override in config.tool_overrides.values()
        if override.capability_kind is not None
    } == {"WEB_FETCH"}


def test_playwright_active_tools_are_browser_automation_not_sandbox() -> None:
    [config] = load_config_file(_CURATED / "playwright.yaml")

    assert config.command == ("npx", "-y", "@playwright/mcp@latest")
    navigate = config.tool_overrides["browser_navigate"].capability_kind
    click = config.tool_overrides["browser_click"].capability_kind
    evaluate = config.tool_overrides["browser_evaluate"].capability_kind
    file_upload = config.tool_overrides["browser_file_upload"].capability_kind
    snapshot = config.tool_overrides["browser_snapshot"].capability_kind
    assert navigate is not None
    assert click is not None
    assert evaluate is not None
    assert file_upload is not None
    assert snapshot is not None
    assert navigate.value == "BROWSER_NAVIGATE"
    assert click.value == "BROWSER_INTERACT"
    assert evaluate.value == "BROWSER_SCRIPT"
    assert file_upload.value == "BROWSER_FILE"
    assert snapshot.value == "BROWSER_READ"


def test_bundled_python_config_includes_specialized_macos_servers() -> None:
    configs = load_config_file(_CURATED / "bundled-python-servers.yaml")
    names = {config.name for config in configs}

    assert {
        "bundled-applescript",
        "bundled-apple-mail",
        "bundled-keynote",
        "bundled-pages",
        "bundled-numbers",
        "bundled-macos",
        "bundled-outlook",
        "bundled-word",
        "bundled-powerpoint",
    } <= names

    outlook = next(config for config in configs if config.name == "bundled-outlook")
    assert outlook.tool_overrides["outlook.create_draft"].capability_kind is not None
    assert outlook.tool_overrides["outlook.create_draft"].target_arg == "to"
    word = next(config for config in configs if config.name == "bundled-word")
    assert word.tool_overrides["word.append_text"].capability_kind is not None
    assert word.tool_overrides["word.append_text"].target_template == "word://frontmost"
    powerpoint = next(config for config in configs if config.name == "bundled-powerpoint")
    assert powerpoint.tool_overrides["powerpoint.start_slideshow"].target_template == (
        "powerpoint://frontmost"
    )


def test_bundled_fs_git_memory_declare_target_arg() -> None:
    """A tool_override with no target_arg/target_template falls back to
    looking up `args["target"]` (adapter.py), which fs/git/memory tools
    never set — every call's capability check then scopes against an
    empty target and a path-scoped grant (e.g. the foreground chat
    defaults' `READ_FS` on `~/Desktop/*`) can never match. This silently
    denies every bundled-fs/git/memory call regardless of the session's
    actual capabilities, with no admission warning (READ_FS isn't a
    destructive/egress kind) to flag it."""
    configs = load_config_file(_CURATED / "bundled-python-servers.yaml")

    fs = next(config for config in configs if config.name == "bundled-fs")
    for tool in ("fs.read", "fs.list", "fs.create", "fs.write", "fs.delete"):
        assert fs.tool_overrides[tool].target_arg == "path", tool

    git = next(config for config in configs if config.name == "bundled-git")
    for tool in ("git.status", "git.log", "git.diff", "git.show", "git.branch_list"):
        assert git.tool_overrides[tool].target_arg == "repo_path", tool

    memory = next(config for config in configs if config.name == "bundled-memory")
    for tool in ("memory.create", "memory.read", "memory.update", "memory.delete"):
        assert memory.tool_overrides[tool].target_arg == "key", tool
    assert memory.tool_overrides["memory.list"].target_arg == "prefix"
