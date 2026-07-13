"""Tests for ${VAR} expansion in upstream_servers env config.

The multi-credential pattern (github-work + github-personal pointing
at the same MCP image with different tokens) requires per-server env
that references operator-shell env vars without writing secrets into
the YAML file.
"""

from __future__ import annotations

import pytest

from capabledeputy.cli._managed_config import resolve_upstream_spawn_command
from capabledeputy.upstream.config import (
    UpstreamServerConfig,
    expand_env_value,
    parse_config,
)


def test_expand_simple() -> None:
    assert expand_env_value("${FOO}", {"FOO": "bar"}) == "bar"


def test_expand_with_default_used() -> None:
    assert expand_env_value("${MISSING:-fallback}", {}) == "fallback"


def test_expand_with_default_overridden() -> None:
    assert expand_env_value("${SET:-fallback}", {"SET": "real"}) == "real"


def test_expand_missing_no_default_becomes_empty() -> None:
    assert expand_env_value("${MISSING}", {}) == ""


def test_expand_multiple_in_one_value() -> None:
    assert (
        expand_env_value(
            "${USER}@${HOST}",
            {"USER": "alice", "HOST": "example.com"},
        )
        == "alice@example.com"
    )


def test_expand_no_var_passthrough() -> None:
    assert expand_env_value("plain", {}) == "plain"


def test_parse_config_expands_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """parse_config should resolve ${VAR} references at config-load time
    so the resolved values reach the subprocess; the YAML never holds
    plaintext secrets."""
    monkeypatch.setenv("MY_TEST_TOKEN", "tok-12345")
    raw = {
        "upstream_servers": [
            {
                "name": "github-test",
                "command": ["echo"],
                "env": {
                    "GITHUB_TOKEN": "${MY_TEST_TOKEN}",
                    "STATIC": "plain-value",
                    "WITH_DEFAULT": "${UNSET_VAR:-fallback}",
                },
            },
        ],
    }
    parsed = parse_config(raw)
    assert len(parsed) == 1
    cfg = parsed[0]
    assert isinstance(cfg, UpstreamServerConfig)
    assert cfg.env["GITHUB_TOKEN"] == "tok-12345"
    assert cfg.env["STATIC"] == "plain-value"
    assert cfg.env["WITH_DEFAULT"] == "fallback"


def test_parse_config_no_env_yields_empty_dict() -> None:
    raw = {"upstream_servers": [{"name": "x", "command": ["true"]}]}
    cfg = parse_config(raw)[0]
    assert cfg.env == {}


def test_multi_credential_pattern_distinct_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two servers with the same command but different ${VAR} refs end
    up with distinct env at the subprocess level — the core enabling
    primitive for the github-work / github-personal pattern."""
    monkeypatch.setenv("WORK_TOKEN", "work-tok")
    monkeypatch.setenv("PERSONAL_TOKEN", "personal-tok")
    raw = {
        "upstream_servers": [
            {
                "name": "github-work",
                "command": ["mcp-server-github"],
                "env": {"GITHUB_TOKEN": "${WORK_TOKEN}"},
            },
            {
                "name": "github-personal",
                "command": ["mcp-server-github"],
                "env": {"GITHUB_TOKEN": "${PERSONAL_TOKEN}"},
            },
        ],
    }
    parsed = parse_config(raw)
    assert len(parsed) == 2
    assert parsed[0].env["GITHUB_TOKEN"] == "work-tok"
    assert parsed[1].env["GITHUB_TOKEN"] == "personal-tok"
    # Tools end up prefixed as github-work.* and github-personal.* per
    # the existing adapter behavior (UpstreamServerConfig.name is the
    # prefix; see adapter.py:134).
    assert parsed[0].name == "github-work"
    assert parsed[1].name == "github-personal"


def test_uvx_command_resolves_to_project_venv(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    uvx = venv_bin / "uvx"
    uvx.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", [str(venv_bin / "capdep")])
    # Isolate from the real installed `capdep`: `_capdep_executable()` calls
    # shutil.which("capdep") first, which under `uv run` (CI) finds the project's
    # actual .venv/bin/capdep and resolves to the real venv's uvx. Restrict PATH
    # to the tmp venv (which has no capdep) so resolution falls through to the
    # cwd-based project-venv detection this test exercises.
    monkeypatch.setenv("PATH", str(venv_bin))

    assert resolve_upstream_spawn_command(("uvx", "kagimcp")) == (
        str(uvx),
        "kagimcp",
    )
