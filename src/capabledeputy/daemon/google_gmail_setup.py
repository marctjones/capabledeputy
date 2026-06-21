"""Daemon-owned setup helpers for the official Google Gmail MCP server."""

from __future__ import annotations

import json
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from anyio.to_thread import run_sync as run_sync_in_worker_thread

from capabledeputy.upstream.config import UpstreamServerConfig
from capabledeputy.upstream.http_auth import oauth_token_cache_path, perform_oauth2_login
from capabledeputy.upstream.server_yaml import ServerYamlConfig

GOOGLE_GMAIL_SERVER = "google-gmail"


@dataclass(frozen=True)
class GmailOAuthPaths:
    config_home: Path
    servers_dir: Path
    server_yaml: Path
    oauth_dir: Path
    client_id_file: Path
    client_secret_file: Path


def gmail_oauth_paths(config_home: Path | None = None) -> GmailOAuthPaths:
    base = config_home or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    capdep = base / "capabledeputy"
    oauth_dir = capdep / "oauth"
    return GmailOAuthPaths(
        config_home=base,
        servers_dir=capdep / "servers.d",
        server_yaml=capdep / "servers.d" / "google-gmail.yaml",
        oauth_dir=oauth_dir,
        client_id_file=oauth_dir / "google-gmail-client-id",
        client_secret_file=oauth_dir / "google-gmail-client-secret",
    )


def gmail_oauth_status(config_home: Path | None = None) -> dict[str, Any]:
    paths = gmail_oauth_paths(config_home)
    server_config = _load_gmail_server_config(paths.server_yaml)
    token_path = _token_path(server_config)
    return {
        "server": GOOGLE_GMAIL_SERVER,
        "configured": paths.server_yaml.is_file(),
        "client_id_configured": paths.client_id_file.is_file(),
        "client_secret_configured": paths.client_secret_file.is_file(),
        "token_configured": token_path.is_file(),
        "server_yaml": str(paths.server_yaml),
        "client_id_file": str(paths.client_id_file),
        "client_secret_file": str(paths.client_secret_file),
        "token_cache": str(token_path),
        "restart_required": True,
    }


def configure_gmail_oauth_client(
    *,
    client_id: str,
    client_secret: str,
    config_home: Path | None = None,
) -> dict[str, Any]:
    """Persist OAuth client settings and Gmail MCP server config.

    Client secrets are written to mode-0600 files under
    ~/.config/capabledeputy/oauth/. The server YAML references those
    files and contains no secret values.
    """
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    if not client_id:
        raise ValueError("Google OAuth client ID is required")
    if not client_secret:
        raise ValueError("Google OAuth client secret is required")

    paths = gmail_oauth_paths(config_home)
    paths.oauth_dir.mkdir(parents=True, exist_ok=True)
    paths.servers_dir.mkdir(parents=True, exist_ok=True)
    _write_secret_file(paths.client_id_file, client_id)
    _write_secret_file(paths.client_secret_file, client_secret)
    paths.server_yaml.write_text(_gmail_server_yaml(paths), encoding="utf-8")
    os.chmod(paths.server_yaml, 0o600)
    return gmail_oauth_status(config_home)


async def run_gmail_oauth_login(
    *,
    open_browser: bool = True,
    timeout_seconds: int = 180,
    config_home: Path | None = None,
) -> dict[str, Any]:
    paths = gmail_oauth_paths(config_home)
    server_config = _load_gmail_server_config(paths.server_yaml)
    if server_config is None or server_config.auth is None:
        raise RuntimeError("Google Gmail MCP OAuth is not configured yet")

    def _login() -> Path:
        return perform_oauth2_login(
            server_config.auth,  # type: ignore[arg-type]
            server_name=server_config.name,
            open_browser=open_browser,
            timeout_seconds=timeout_seconds,
        )

    token_path = await run_sync_in_worker_thread(_login)
    status = gmail_oauth_status(config_home)
    return {**status, "token_cache": str(token_path)}


def _load_gmail_server_config(path: Path) -> UpstreamServerConfig | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return None
    parsed = ServerYamlConfig.from_dict(raw, filename=str(path))
    return parsed.server_config


def _token_path(server_config: UpstreamServerConfig | None) -> Path:
    if server_config is not None and server_config.auth is not None:
        return oauth_token_cache_path(server_config.auth, server_config.name)
    return gmail_oauth_paths().oauth_dir / "google-gmail.json"


def _write_secret_file(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)
        f.write("\n")


def _gmail_server_yaml(paths: GmailOAuthPaths) -> str:
    body = {
        "schema_version": 1,
        "name": GOOGLE_GMAIL_SERVER,
        "transport": "streamable_http",
        "url": "https://gmailmcp.googleapis.com/mcp/v1",
        "auth": {
            "type": "oauth2",
            "client_id_file": str(paths.client_id_file),
            "client_secret_file": str(paths.client_secret_file),
            "token_cache": str(paths.oauth_dir / "google-gmail.json"),
            "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.compose",
            ],
            "extra_authorize_params": {
                "access_type": "offline",
                "prompt": "consent",
            },
        },
        "inherent_labels": ["confidential.personal", "untrusted.user_input"],
        "disabled_kinds": ["SEND_EMAIL"],
        "tool_overrides": {
            "create_draft": {
                "capability_kind": "GMAIL_DRAFT",
                "additional_labels": ["confidential.personal"],
                "target_arg": "to",
            },
            "create_label": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
            "get_thread": {
                "capability_kind": "GMAIL_READ",
                "additional_labels": ["confidential.personal", "untrusted.user_input"],
            },
            "label_message": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
            "label_thread": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
            "list_drafts": {
                "capability_kind": "GMAIL_READ",
                "additional_labels": ["confidential.personal"],
            },
            "list_labels": {
                "capability_kind": "GMAIL_READ",
                "additional_labels": ["confidential.personal"],
            },
            "search_threads": {
                "capability_kind": "GMAIL_READ",
                "additional_labels": ["confidential.personal", "untrusted.user_input"],
            },
            "unlabel_message": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
            "unlabel_thread": {
                "capability_kind": "MODIFY_FS",
                "additional_labels": ["confidential.personal"],
            },
        },
        "strict": True,
    }
    rendered = yaml.safe_dump(body, sort_keys=False)
    header = textwrap.dedent(
        """\
        # Managed by CapDep daemon setup.google_gmail.configure_oauth.
        # OAuth client secret values live in mode-0600 files under ../oauth/.
        """,
    )
    return header + rendered


def redacted_gmail_oauth_payload(status: dict[str, Any]) -> dict[str, Any]:
    """Return an audit-safe shape for setup actions."""
    return {
        key: value for key, value in status.items() if key not in {"client_id", "client_secret"}
    }


def token_cache_summary(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"present": False}
    return {
        "present": bool(data.get("access_token")),
        "has_refresh_token": bool(data.get("refresh_token")),
        "expires_at": data.get("expires_at"),
    }
