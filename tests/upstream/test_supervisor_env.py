from __future__ import annotations

from capabledeputy.upstream.config import UpstreamServerConfig
from capabledeputy.upstream.supervisor import build_stdio_env


def test_build_stdio_env_excludes_parent_process_secrets() -> None:
    config = UpstreamServerConfig(name="demo", command=("server",))
    env = build_stdio_env(
        config,
        {
            "PATH": "/usr/bin",
            "HOME": "/Users/operator",
            "LANG": "en_US.UTF-8",
            "ANTHROPIC_API_KEY": "hosted-model-secret",
            "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/google.json",
            "GITHUB_TOKEN": "repo-token",
            "CAPDEP_STATE_DB": "/tmp/capdep.sqlite3",
        },
    )

    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/Users/operator",
        "LANG": "en_US.UTF-8",
    }


def test_build_stdio_env_allows_explicit_server_env_overrides() -> None:
    config = UpstreamServerConfig(
        name="github-work",
        command=("github-mcp-server",),
        env={
            "GITHUB_TOKEN": "operator-approved-token",
            "PATH": "/opt/capdep/bin",
        },
    )
    env = build_stdio_env(
        config,
        {
            "PATH": "/usr/bin",
            "HOME": "/Users/operator",
            "GITHUB_TOKEN": "ambient-token-that-must-not-leak",
        },
    )

    assert env["PATH"] == "/opt/capdep/bin"
    assert env["HOME"] == "/Users/operator"
    assert env["GITHUB_TOKEN"] == "operator-approved-token"
