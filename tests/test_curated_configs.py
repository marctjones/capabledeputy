"""Guard: the shipped curated MCP catalog stays parseable and locked.

These configs let CapableDeputy drive real upstream MCP servers behind
the policy engine. The catalog's security guarantee rests on three
invariants this test pins:

  - every config parses (no CapabilityKind / Label typo silently
    dropping a tool override),
  - every server is strict (fail-closed admission), and
  - the Google Workspace configs have explicit
    override for every tool it declares (nothing destructive there may
    ride on inference).
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


@pytest.mark.parametrize(
    "filename",
    ["google-workspace.yaml", "google-workspace-community.yaml"],
)
def test_google_workspace_pins_every_declared_tool(filename: str) -> None:
    """Workspace producers can evolve; nothing may rely on inference."""
    configs = load_config_file(_CURATED / filename)
    for c in configs:
        assert c.tool_overrides, f"{c.name} declares no explicit tool overrides"
        for name, ov in c.tool_overrides.items():
            assert ov.capability_kind is not None, (
                f"{c.name}.{name} override has no capability_kind"
            )


def test_official_google_workspace_uses_gmail_draft_capability() -> None:
    configs = load_config_file(_CURATED / "google-workspace.yaml")
    gmail = next(config for config in configs if config.name == "google-gmail")
    calendar = next(config for config in configs if config.name == "google-calendar")

    assert gmail.tool_overrides["create_draft"].capability_kind is not None
    assert gmail.tool_overrides["create_draft"].capability_kind.value == "GMAIL_DRAFT"
    assert gmail.tool_overrides["create_draft"].target_arg == "to"
    assert calendar.tool_overrides["create_event"].target_template == (
        "gcal://calendar/{calendar_id}/events/attendees/{attendees}"
    )


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
        "google-workspace.yaml",
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
    } <= names
